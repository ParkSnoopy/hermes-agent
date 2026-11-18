---
sidebar_position: 2
title: "Installation"
description: "Install Hermes Agent from a local Linux source checkout"
---

# Installation

Hermes runs on Linux. See [Platform Support](./platform-support.md) for architectures and distribution notes.

## Linux source checkout

Zsh is the primary interactive shell for this workflow.
Bash remains supported and is required for internal scripts.

From the root of your local source checkout, activate the managed environment and run setup:

```zsh
source ./activate
hermes setup
hermes
```

The activation script prepares project-managed dependencies. It may require network access and a native compiler. See [Package management](../reference/package-management.md#developer-workflow) for prerequisites and the development workflow.

Choose a model provider in `hermes setup` or later with `hermes model`. Configure messaging separately with `hermes gateway setup`. User data is stored separately from the checkout; `HERMES_HOME` selects the data directory. Do not remove it to repair the application checkout.

For aarch64 Android devices, see the [Termux guide](./termux.md).
