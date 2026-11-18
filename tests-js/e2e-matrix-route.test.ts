import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from 'vitest'

const script: string = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../scripts/sandbox/generate-e2e-matrix.mjs')
const TAGS: string = JSON.stringify([{ ref: 'v2026.6.19' }])

interface Leg {
  name: string
  install_method: string
  update_method: string
  install_ref: string
  update_ref: string
}
type Matrices = Record<'linux', { include: Leg[] }>

function generate(route: string): Matrices {
  return JSON.parse(execFileSync(process.execPath, [script, '--tags', TAGS, '--route', route], { encoding: 'utf8' })) as Matrices
}

function legNames(matrices: Matrices): string[] {
  return Object.values(matrices).flatMap(m => m.include.map(leg => leg.name))
}

test('a leg name, or the job name GitHub shows for it, runs exactly that leg', () => {
  const leg: Leg | undefined = generate('all').linux.include.find(l =>
    l.name.includes('installer-script -> hermes-update')
  )

  expect(leg).toBeDefined()
  expect(legNames(generate(leg!.name))).toEqual([leg!.name])
  expect(legNames(generate(`${leg!.name} / e2e`))).toEqual([leg!.name])
})

test('a source preset runs the Linux matrix', () => {
  const all: Matrices = generate('all')
  const installer: Matrices = generate('installer')

  expect(legNames(installer)).toEqual(all.linux.include.map(l => l.name))
  expect(Object.keys(all)).toEqual(['linux'])
})

test('all combines each Linux source update method with both starts', () => {
  const legs = generate('all').linux.include
  expect(legs.map(({ install_method, update_method, install_ref, update_ref }) =>
    [install_method, update_method, install_ref, update_ref]
  )).toEqual([
    ['installer-script', 'installer-script', 'v2026.6.19', 'HEAD'],
    ['installer-script', 'installer-script', 'HEAD', 'NEXT'],
    ['installer-script', 'hermes-update', 'v2026.6.19', 'HEAD'],
    ['installer-script', 'hermes-update', 'HEAD', 'NEXT'],
  ])
})

test('a route that selects nothing fails instead of running an empty green matrix', () => {
  expect(() => generate('windows-desktop')).toThrow()
})

test('the pr route runs one script install updated by hermes update per Linux start', () => {
  const pr: Matrices = generate('pr')

  expect(legNames(pr).sort()).toEqual([
    'linux: installer-script -> hermes-update (HEAD -> NEXT)',
    'linux: installer-script -> hermes-update (v2026.6.19 -> HEAD)',
  ])
})
