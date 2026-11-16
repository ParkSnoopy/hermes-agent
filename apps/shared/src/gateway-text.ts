/** User-facing text shared by gateway clients. */
export const gatewayTextEn = {
  canonical: {
    launch: {
      noTuiPolicy: 'gateway does not support tui session policy; update/restart the gateway',
      unsupportedOptions: (options: string) => `gateway does not support TUI launch options: ${options}`,
      // The same refusals for a client that launches with another source or names itself.
      /** {0} = session source */
      noSourcePolicy: (source: string) => `gateway does not support ${source} session policy; update/restart the gateway`,
      /** {0} = comma-separated option names */
      unsupportedSessionOptions: (options: string) => `gateway does not support these session options: ${options}`
    }
  },
  libText: {
    // Transport-drop reasons that reject in-flight RPCs and therefore surface in the transcript.
    gateway: {
      restarting: 'gateway restarting',
      exited: 'gateway exited',
      /** {0} = exit code */
      exitedWithCode: (code: string) => `gateway exited (${code})`,
      websocketUnavailable: 'gateway websocket unavailable',
      websocketConnectionFailed: 'gateway websocket connection failed',
      /** {0} = websocket close code */
      websocketClosedDuringConnect: (code: string) => `gateway websocket closed (${code}) during connect`,
      websocketClosed: 'gateway websocket closed',
      /** {0} = websocket close code */
      websocketClosedWithCode: (code: string) => `gateway websocket closed (${code})`,
      websocketStartupFailed: 'gateway websocket startup failed',
      attachUrlChanged: 'gateway attach url changed',
      closed: 'gateway closed'
    }
  }
}
