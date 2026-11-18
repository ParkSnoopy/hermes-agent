---
title: "Network Egress Isolation"
description: "Consider network controls when deploying Hermes"
---

# Network Egress Isolation

Local terminal commands run with the Hermes process user's permissions and network access.
Hermes does not itself isolate those commands.
An operator can add independent network controls to limit which endpoints a deployment can reach, reducing the risk of data exfiltration by untrusted content.

Allow the endpoints required by the selected model provider and messaging platforms.
Proxy environment variables alone do not prevent commands from bypassing a proxy; enforce restrictions at the network boundary and verify both allowed and denied destinations from the execution environment.
Consider DNS traffic as well as HTTP, and review the controls whenever services or credentials change.
Network restrictions do not by themselves restrict filesystem access or guarantee containment.

See the [security guide](../security.md) and [SECURITY.md](https://github.com/NousResearch/hermes-agent/blob/main/SECURITY.md) for the trust model.