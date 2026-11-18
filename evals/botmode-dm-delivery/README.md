# Native Bot Mode delivery probe

Real Electron, production Python backend and tool execution, disposable HOME/HERMES_HOME,
loopback scripted inference (no paid model). Linux seat fixture; run from repository root:

```sh
pnpm install --frozen-lockfile
cp evals/botmode-dm-delivery/probe-dm-delivery.spec.ts apps/desktop/e2e/
git apply evals/botmode-dm-delivery/mock-trigger.patch
(cd apps/desktop && pnpm run build)
(cd apps/desktop && DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_cnpsqU \
  XDG_RUNTIME_DIR=/run/user/1000 VIRTUAL_ENV="$VIRTUAL_ENV" \
  HERMES_DESKTOP_CDP_PORT=off pnpm exec playwright test e2e/probe-dm-delivery.spec.ts --reporter=list)
git apply -R evals/botmode-dm-delivery/mock-trigger.patch
rm apps/desktop/e2e/probe-dm-delivery.spec.ts
```

Use the current seat's actual Xauthority path and an existing runtime venv.
Artifacts default to `/tmp/botmode-dm-review/native` (override with `BOT_DM_EVIDENCE`); sandbox path is printed. The
fixture's generated hermes shim pins every child to this checkout, not an installed launcher.

## Verified results

Base `cf35e7351e770`: nested beta quiet CLI executes real `message_agent` to the named
alpha Desktop owner. Admission, execution and reply succeed, and the quiet sender
receives its completion. A reload then shows the attributed incoming message. The
live pre-reload view showed the reply but omitted the incoming row in this fixture.
The supported-owner nested case passed independently on base (**1 passed (40.0s)**).
