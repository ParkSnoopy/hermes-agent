import type { RunExternalProcess } from '@hermes/ink'
import type { SetupStatusResponse } from '@hermes/shared/gateway-types'

import { sessionEn } from '../copy/session.js'
import type { LaunchResult } from '../lib/externalCli.js'

import type { SlashHandlerContext } from './interfaces.js'
import { patchUiState } from './uiStore.js'

export interface RunExternalSetupOptions {
  args: string[]
  ctx: Pick<SlashHandlerContext, 'gateway' | 'session' | 'transcript'>
  done: string
  launcher: (args: string[]) => Promise<LaunchResult>
  suspend: (run: RunExternalProcess) => Promise<void>
}

export async function runExternalSetup({ args, ctx, done, launcher, suspend }: RunExternalSetupOptions) {
  const { gateway, session, transcript } = ctx

  transcript.sys(sessionEn.session.handoff.launching(args.join(' ')))
  patchUiState({ status: sessionEn.session.status.setupRunning })

  let result: LaunchResult = { code: null }

  await suspend(async () => {
    result = await launcher(args)
  })

  if (result.error) {
    transcript.sys(sessionEn.session.handoff.launchError(result.error))
    patchUiState({ status: sessionEn.session.status.setupRequired })

    return
  }

  if (result.code !== 0) {
    transcript.sys(sessionEn.session.handoff.exitedWithCode(args[0], result.code))
    patchUiState({ status: sessionEn.session.status.setupRequired })

    return
  }

  const setup = await gateway.rpc<SetupStatusResponse>('setup.status', {})

  if (setup?.provider_configured === false) {
    transcript.sys(sessionEn.session.handoff.stillNoProvider)
    patchUiState({ status: sessionEn.session.status.setupRequired })

    return
  }

  transcript.sys(done)
  session.newSession()
}
