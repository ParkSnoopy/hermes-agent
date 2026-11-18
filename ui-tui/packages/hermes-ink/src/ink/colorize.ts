import { spawnSync } from 'node:child_process'

import chalk, { type ColorSupportLevel } from 'chalk'

import type { Color, TextStyles } from './styles.js'

const TRUECOLOR_ON = /^(?:1|true|yes|on)$/i
const TRUECOLOR_OFF = /^(?:0|false|no|off)$/i

function tmuxClientFeatures(pane: string | undefined): string {
  if (!pane || !process.stdout.isTTY) {
    return ''
  }

  // COLORTERM can be inherited from the outer terminal even when tmux will
  // quantize RGB. Ask tmux what it can actually emit for the current pane.
  const result = spawnSync('tmux', ['display-message', '-p', '-t', pane, '#{client_termfeatures}'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
    timeout: 200
  })

  return result.status === 0 ? result.stdout.trim() : ''
}

export function terminalColorLevel(
  detected: ColorSupportLevel,
  env: NodeJS.ProcessEnv = process.env,
  readTmuxFeatures = tmuxClientFeatures
): ColorSupportLevel {
  if ('NO_COLOR' in env || detected === 0) {
    return 0
  }

  const override = (env.HERMES_TUI_TRUECOLOR ?? '').trim()
  const indexed = detected === 3 ? 2 : detected

  if (TRUECOLOR_OFF.test(override)) {
    return indexed
  }

  if (TRUECOLOR_ON.test(override)) {
    return 3
  }

  const hasForceColor = 'FORCE_COLOR' in env

  if (env.TMUX) {
    if (hasForceColor && detected < 3) {
      return detected
    }

    return readTmuxFeatures(env.TMUX_PANE).split(',').includes('RGB') ? 3 : indexed
  }

  // xterm.js can render RGB even on hosts which only advertise xterm-256color.
  return !hasForceColor && env.TERM_PROGRAM === 'vscode' && detected === 2 ? 3 : detected
}

export function shouldUseRichEightBitDowngradeForLegacyAppleTerminal(
  env: NodeJS.ProcessEnv = process.env,
  level = chalk.level
): boolean {
  const termProgram = (env.TERM_PROGRAM ?? '').trim()
  const truecolorOverride = TRUECOLOR_ON.test((env.HERMES_TUI_TRUECOLOR ?? '').trim())
  const advertisesTruecolor = /^(?:truecolor|24bit)$/i.test((env.COLORTERM ?? '').trim())

  return (
    termProgram === 'Apple_Terminal' &&
    !truecolorOverride &&
    !advertisesTruecolor &&
    !('FORCE_COLOR' in env) &&
    level === 2
  )
}

export function richEightBitColorNumber(red: number, green: number, blue: number): number {
  const rn = red / 255
  const gn = green / 255
  const bn = blue / 255
  const max = Math.max(rn, gn, bn)
  const min = Math.min(rn, gn, bn)
  const lightness = (max + min) / 2
  const saturation = max === min ? 0 : lightness > 0.5 ? (max - min) / (2 - max - min) : (max - min) / (max + min)

  if (saturation < 0.15) {
    const gray = Math.round(lightness * 25)

    return gray === 0 ? 16 : gray === 25 ? 231 : 231 + gray
  }

  const sixRed = red < 95 ? red / 95 : 1 + (red - 95) / 40
  const sixGreen = green < 95 ? green / 95 : 1 + (green - 95) / 40
  const sixBlue = blue < 95 ? blue / 95 : 1 + (blue - 95) / 40

  return 16 + 36 * Math.round(sixRed) + 6 * Math.round(sixGreen) + Math.round(sixBlue)
}

// Resolve once before rendering the first frame.
chalk.level = terminalColorLevel(chalk.level)
export const CHALK_USES_RICH_EIGHT_BIT_DOWNGRADE = shouldUseRichEightBitDowngradeForLegacyAppleTerminal()

export type ColorType = 'foreground' | 'background'

const RGB_REGEX = /^rgb\(\s?(\d+),\s?(\d+),\s?(\d+)\s?\)$/
const ANSI_REGEX = /^ansi256\(\s?(\d+)\s?\)$/
const HEX_REGEX = /^#[0-9a-fA-F]{6}$/

export const colorize = (str: string, color: string | undefined, type: ColorType): string => {
  if (!color) {
    return str
  }

  if (color.startsWith('ansi:')) {
    const value = color.substring('ansi:'.length)

    switch (value) {
      case 'black':
        return type === 'foreground' ? chalk.black(str) : chalk.bgBlack(str)

      case 'red':
        return type === 'foreground' ? chalk.red(str) : chalk.bgRed(str)

      case 'green':
        return type === 'foreground' ? chalk.green(str) : chalk.bgGreen(str)

      case 'yellow':
        return type === 'foreground' ? chalk.yellow(str) : chalk.bgYellow(str)

      case 'blue':
        return type === 'foreground' ? chalk.blue(str) : chalk.bgBlue(str)

      case 'magenta':
        return type === 'foreground' ? chalk.magenta(str) : chalk.bgMagenta(str)

      case 'cyan':
        return type === 'foreground' ? chalk.cyan(str) : chalk.bgCyan(str)

      case 'white':
        return type === 'foreground' ? chalk.white(str) : chalk.bgWhite(str)

      case 'blackBright':
        return type === 'foreground' ? chalk.blackBright(str) : chalk.bgBlackBright(str)

      case 'redBright':
        return type === 'foreground' ? chalk.redBright(str) : chalk.bgRedBright(str)

      case 'greenBright':
        return type === 'foreground' ? chalk.greenBright(str) : chalk.bgGreenBright(str)

      case 'yellowBright':
        return type === 'foreground' ? chalk.yellowBright(str) : chalk.bgYellowBright(str)

      case 'blueBright':
        return type === 'foreground' ? chalk.blueBright(str) : chalk.bgBlueBright(str)

      case 'magentaBright':
        return type === 'foreground' ? chalk.magentaBright(str) : chalk.bgMagentaBright(str)

      case 'cyanBright':
        return type === 'foreground' ? chalk.cyanBright(str) : chalk.bgCyanBright(str)

      case 'whiteBright':
        return type === 'foreground' ? chalk.whiteBright(str) : chalk.bgWhiteBright(str)
    }
  }

  if (color.startsWith('#')) {
    if (HEX_REGEX.test(color) && CHALK_USES_RICH_EIGHT_BIT_DOWNGRADE) {
      const value = Number.parseInt(color.slice(1), 16)
      const red = (value >> 16) & 0xff
      const green = (value >> 8) & 0xff
      const blue = value & 0xff
      const ansi = richEightBitColorNumber(red, green, blue)

      return type === 'foreground' ? chalk.ansi256(ansi)(str) : chalk.bgAnsi256(ansi)(str)
    }

    return type === 'foreground' ? chalk.hex(color)(str) : chalk.bgHex(color)(str)
  }

  if (color.startsWith('ansi256')) {
    const matches = ANSI_REGEX.exec(color)

    if (!matches) {
      return str
    }

    const value = Number(matches[1])

    return type === 'foreground' ? chalk.ansi256(value)(str) : chalk.bgAnsi256(value)(str)
  }

  if (color.startsWith('rgb')) {
    const matches = RGB_REGEX.exec(color)

    if (!matches) {
      return str
    }

    const firstValue = Number(matches[1])
    const secondValue = Number(matches[2])
    const thirdValue = Number(matches[3])

    if (CHALK_USES_RICH_EIGHT_BIT_DOWNGRADE) {
      const ansi = richEightBitColorNumber(firstValue, secondValue, thirdValue)

      return type === 'foreground' ? chalk.ansi256(ansi)(str) : chalk.bgAnsi256(ansi)(str)
    }

    return type === 'foreground'
      ? chalk.rgb(firstValue, secondValue, thirdValue)(str)
      : chalk.bgRgb(firstValue, secondValue, thirdValue)(str)
  }

  return str
}

/**
 * Apply TextStyles to a string using chalk.
 * This is the inverse of parsing ANSI codes - we generate them from structured styles.
 * Theme resolution happens at component layer, not here.
 */
export function applyTextStyles(text: string, styles: TextStyles): string {
  let result = text

  // Apply styles in reverse order of desired nesting.
  // chalk wraps text so later calls become outer wrappers.
  // Desired order (outermost to innermost):
  //   background > foreground > text modifiers
  // So we apply: text modifiers first, then foreground, then background last.

  if (styles.inverse) {
    result = chalk.inverse(result)
  }

  if (styles.strikethrough) {
    result = chalk.strikethrough(result)
  }

  if (styles.underline) {
    result = chalk.underline(result)
  }

  if (styles.italic) {
    result = chalk.italic(result)
  }

  if (styles.bold) {
    result = chalk.bold(result)
  }

  if (styles.dim) {
    result = chalk.dim(result)
  }

  if (styles.color) {
    // Color is now always a raw color value (theme resolution happens at component layer)
    result = colorize(result, styles.color, 'foreground')
  }

  if (styles.backgroundColor) {
    // backgroundColor is now always a raw color value
    result = colorize(result, styles.backgroundColor, 'background')
  }

  return result
}

/**
 * Apply a raw color value to text.
 * Theme resolution should happen at component layer, not here.
 */
export function applyColor(text: string, color: Color | undefined): string {
  if (!color) {
    return text
  }

  return colorize(text, color, 'foreground')
}
