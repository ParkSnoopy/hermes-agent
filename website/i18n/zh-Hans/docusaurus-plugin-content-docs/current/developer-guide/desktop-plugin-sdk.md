---
title: "Desktop 插件 SDK"
description: "使用 Desktop 插件扩展 Hermes 界面"
---

# Desktop 插件 SDK

Desktop 插件可为应用添加面板、页面、状态栏项目、命令和主题。插件通过 SDK 的 `host` 访问会话状态、通知以及 gateway JSON-RPC，并通过 `ctx.register` 注册界面贡献。插件在应用进程中运行，应仅安装可信代码。

完整 API、示例、权限模型与故障排查见 [Desktop Plugin SDK（英文原文）](../../../../../docs/developer-guide/desktop-plugin-sdk.md)。Python 插件、工具和钩子另见 [构建 Hermes 插件](./plugins/index.md)。
