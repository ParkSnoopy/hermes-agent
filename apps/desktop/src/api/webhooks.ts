import type { WebhookCreatePayload, WebhookCreateResponse, WebhookEnableResponse, WebhooksResponse } from '@/types/hermes'

import { hermesApi, profileScoped } from './client'

export function getWebhooks(profile?: null | string): Promise<WebhooksResponse> {
  return hermesApi<WebhooksResponse>({ ...profileScoped(profile), path: '/api/webhooks' })
}

export function enableWebhooks(profile?: null | string): Promise<WebhookEnableResponse> {
  return hermesApi<WebhookEnableResponse>({ ...profileScoped(profile), path: '/api/webhooks/enable', method: 'POST' })
}

export function createWebhook(body: WebhookCreatePayload, profile?: null | string): Promise<WebhookCreateResponse> {
  return hermesApi<WebhookCreateResponse>({ ...profileScoped(profile), path: '/api/webhooks', method: 'POST', body })
}

export function deleteWebhook(name: string, profile?: null | string): Promise<{ ok: boolean }> {
  return hermesApi<{ ok: boolean }>({ ...profileScoped(profile), path: `/api/webhooks/${encodeURIComponent(name)}`, method: 'DELETE' })
}

export function setWebhookEnabled(name: string, enabled: boolean, profile?: null | string): Promise<{ enabled: boolean; name: string; ok: boolean }> {
  return hermesApi<{ enabled: boolean; name: string; ok: boolean }>({
    ...profileScoped(profile), path: `/api/webhooks/${encodeURIComponent(name)}/enabled`, method: 'PUT', body: { enabled }
  })
}