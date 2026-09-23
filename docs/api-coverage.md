# API coverage

Every panel screen and the API route behind it. A screen without a route
is a bug; a route without a row here fails `tests/test_api_coverage.py`,
which checks both directions against the OpenAPI schema. Rows marked `—`
are deliberately panel-only, with the reason beside them.

## Versioned API (`/api/v1`)

| Panel screen | Route |
| --- | --- |
| Clients | `GET /api/v1/clients` |
| Clients → New | `POST /api/v1/clients` |
| Client detail | `GET /api/v1/clients/{client_id}` |
| Client detail → edit | `PATCH /api/v1/clients/{client_id}` |
| Client detail → contacts | `GET /api/v1/clients/{client_id}/contacts` |
| Client detail → contacts | `POST /api/v1/clients/{client_id}/contacts` |
| Client detail → contacts | `GET /api/v1/clients/{client_id}/contacts/{contact_id}` |
| Client detail → contacts | `PATCH /api/v1/clients/{client_id}/contacts/{contact_id}` |
| Client detail → tags | `GET /api/v1/clients/{client_id}/tags` |
| Client detail → tags | `POST /api/v1/clients/{client_id}/tags` |
| Client detail → tags | `PATCH /api/v1/clients/{client_id}/tags/{tag_id}` |
| Client detail → tags | `DELETE /api/v1/clients/{client_id}/tags/{tag_id}` |
| Client detail → channels | `GET /api/v1/clients/{client_id}/channels` |
| Client detail → pipeline | `GET /api/v1/clients/{client_id}/pipeline/board` |
| Client detail → pipeline | `GET /api/v1/clients/{client_id}/pipeline/stages` |
| Client detail → pipeline | `POST /api/v1/clients/{client_id}/pipeline/stages` |
| Client detail → pipeline | `PATCH /api/v1/clients/{client_id}/pipeline/stages/{stage_id}` |
| Client detail → pipeline | `POST /api/v1/clients/{client_id}/pipeline/stages/reorder` |
| Client detail → pipeline | `DELETE /api/v1/clients/{client_id}/pipeline/stages/{stage_id}` |
| Client detail → pipeline | `POST /api/v1/clients/{client_id}/pipeline/leads` |
| Client detail → calendar | `GET /api/v1/clients/{client_id}/calendar` |
| Client detail → calendar | `GET /api/v1/clients/{client_id}/calendar/events` |
| Client detail → calendar | `POST /api/v1/clients/{client_id}/calendar/members` |
| Client detail → calendar | `DELETE /api/v1/clients/{client_id}/calendar/members/{member_id}` |
| Agents | `GET /api/v1/clients/{client_id}/agents` |
| Agents → New | `POST /api/v1/clients/{client_id}/agents` |
| Agent detail | `GET /api/v1/clients/{client_id}/agents/{agent_id}` |
| Agent detail → edit | `PATCH /api/v1/clients/{client_id}/agents/{agent_id}` |
| Agent detail → delete | `DELETE /api/v1/clients/{client_id}/agents/{agent_id}` |
| Agent detail → prompt | `GET /api/v1/clients/{client_id}/agents/{agent_id}/prompt` |
| Agent detail → knowledge | `GET /api/v1/clients/{client_id}/agents/{agent_id}/documents` |
| Agent detail → knowledge | `POST /api/v1/clients/{client_id}/agents/{agent_id}/documents` |
| Agent detail → knowledge | `DELETE /api/v1/clients/{client_id}/agents/{agent_id}/documents/{document_id}` |
| Agent detail → knowledge | `POST /api/v1/clients/{client_id}/agents/{agent_id}/documents/reindex` |
| Agent detail → knowledge | `GET /api/v1/clients/{client_id}/agents/{agent_id}/qa` |
| Agent detail → knowledge | `POST /api/v1/clients/{client_id}/agents/{agent_id}/qa` |
| Agent detail → knowledge | `DELETE /api/v1/clients/{client_id}/agents/{agent_id}/qa/{qa_id}` |
| Channels | `GET /api/v1/clients/{client_id}/channels` |
| Channel setup (any) → status | `GET /api/v1/clients/{client_id}/channels` |
| Inbox | `GET /api/v1/clients/{client_id}/conversations` |
| Inbox → thread | `GET /api/v1/clients/{client_id}/conversations/{conversation_id}` |
| Inbox → thread | `GET /api/v1/clients/{client_id}/conversations/{conversation_id}/messages` |
| Inbox → reply | `POST /api/v1/clients/{client_id}/conversations/{conversation_id}/reply` |
| Inbox → take over / hand back | `PATCH /api/v1/clients/{client_id}/conversations/{conversation_id}/mode` |
| Inbox → resolve / reopen | `PATCH /api/v1/clients/{client_id}/conversations/{conversation_id}/status` |
| Inbox → move deal | `PATCH /api/v1/clients/{client_id}/conversations/{conversation_id}/pipeline` |
| Reports | `GET /api/v1/clients/{client_id}/reports/costs` |
| Reports | `GET /api/v1/clients/{client_id}/reports/replies` |
| Reports | `GET /api/v1/clients/{client_id}/reports/operations` |

## Credential management (panel API, unversioned)

Credentials are managed by people, so these live outside `/api/v1`:

| Panel screen | Route |
| --- | --- |
| Settings → API integrations | `GET /api/integrations/scopes` |
| Settings → API integrations | `GET /api/integrations` |
| Settings → API integrations | `POST /api/integrations` |
| Settings → API integrations | `PATCH /api/integrations/{integration_id}` |
| Settings → API integrations | `DELETE /api/integrations/{integration_id}` |
| Settings → API integrations | `GET /api/integrations/{integration_id}/tokens` |
| Settings → API integrations | `POST /api/integrations/{integration_id}/tokens` |
| Settings → API integrations | `DELETE /api/integrations/{integration_id}/tokens/{token_id}` |
| Settings → API integrations → OAuth | `POST /api/integrations/{integration_id}/oauth-client` |
| Settings → API integrations → webhooks | `GET /api/integrations/{integration_id}/webhooks` |
| Settings → API integrations → webhooks | `POST /api/integrations/{integration_id}/webhooks` |
| Settings → API integrations → webhooks | `DELETE /api/integrations/{integration_id}/webhooks/{subscription_id}` |
| Settings → API integrations → webhooks | `GET /api/integrations/{integration_id}/webhooks/{subscription_id}/deliveries` |
| Settings → API integrations → webhooks | `POST /api/integrations/{integration_id}/webhooks/{subscription_id}/deliveries/{delivery_id}/replay` |

## OAuth (panel API, unversioned)

| Panel screen | Route |
| --- | --- |
| OAuth consent → client lookup | `GET /api/oauth/client` |
| OAuth consent → approve / deny | `POST /api/oauth/authorize` |
| OAuth consent → code and refresh exchange | `POST /api/oauth/token` |
| OAuth consent → revoke | `POST /api/oauth/revoke` |

## Deliberately panel-only
| Screen | Reason |
| --- | --- |
| Dashboard (`/`) | Agency analytics; the dashboard routes stay closed to tokens. |
| Agent detail → tools | Tool definitions hold encrypted headers; live MCP discovery is a panel gesture. |
| Agent detail → escalation | Routing to teams and people is internal mechanics. |
| Channel setup → connect | OAuth handshakes and per-line secrets are panel gestures. |
| Calendar connect link | Google OAuth handshake. |
| Client detail → portal users, teams | Identity management for the portal; own credential model. |
| Client detail → templates | Meta-side objects managed against the provider. |
| Client detail → logo, custom domain | Files and DNS verification are panel gestures. |
| Client detail → API tab | Same credential management as Settings, above. |
| Reports → CSV export | File download; the JSON rows are versioned. |
| Tag routing to team/person | Internal mechanics; rename and recolor are versioned. |
| Settings → agency, AI key | Closed to tokens by design. |
| Login, OAuth consent | Credential flows, like `/api/auth`. |
| Playground | Rehearsals leave no rows behind. |
| Portal, widget, mobile | Their own credential model; the sequel to this plan. |
