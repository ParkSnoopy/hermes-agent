/** Ink's gateway client: `@hermes/shared/gateway-client` wired to the `hermes --tui` launcher (its
 *  bootstrap, `HERMES_TUI_*` overrides, launch tool-progress pin and crash-log breadcrumbs). */
import { type ConnectGateway, GatewayClient as SharedGatewayClient } from '@hermes/shared/gateway-client'
import { WebSocket as UndiciWebSocket } from 'undici'

import { launchToolProgress } from './launchOptions.js'
import { recordParentLifecycle } from './lib/parentLog.js'
import { bootstrapLocalGateway, isTerminalBootstrapFailure } from './localGatewayBootstrap.js'

const STARTUP_TIMEOUT_MS = Math.max(5000, parseInt(process.env.HERMES_TUI_STARTUP_TIMEOUT_MS ?? '15000', 10) || 15000)
const REQUEST_TIMEOUT_MS = Math.max(30000, parseInt(process.env.HERMES_TUI_RPC_TIMEOUT_MS ?? '120000', 10) || 120000)


export class GatewayClient extends SharedGatewayClient {
  constructor(bootstrap: ConnectGateway = bootstrapLocalGateway) {
    super({

      connect: bootstrap,
      fallbackWebSocket: UndiciWebSocket as unknown as typeof WebSocket,
      terminalConnectFailure: error =>
        isTerminalBootstrapFailure(error.message) ? error.message.match(/^gateway unavailable: .*$/m)?.[0] : undefined,
      onLifecycle: recordParentLifecycle,
      requestTimeoutMs: REQUEST_TIMEOUT_MS,

      startupTimeoutMs: STARTUP_TIMEOUT_MS,

      toolProgress: () => launchToolProgress()
    })
  }
}
