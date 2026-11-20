# nanoMuse Cloud

A small relay that lets someone use nanoMuse without owning an API key. Sign up
with an e-mail address (a phone number takes its code from the relay log), get a starter allowance, and
the app talks to this server the way it would talk to any OpenAI-compatible
provider. Your own key still works exactly as before — this is the "start now"
path, not a replacement for bring-your-own-key.

It is a single Python process over SQLite. Anyone can run one: the app only needs
its base URL.

## What it does

- **Sign-up by code.** `POST /v1/auth/code` sends a six-digit code to an e-mail
  (SMTP); on a development relay (`CODE_SENDER=log`) the code goes to the log. `POST /v1/auth/verify` exchanges it for an
  `nm_…` API key. One phone/e-mail is one account with one starter grant; a
  second device signing in with the same number gets a second key, not a second
  grant. `POST /v1/auth/session-key` (0.13) turns a key into one that lapses on
  its own (`ttl_s`, 90 days at most) — what nanoMuse Web starts a person's
  container with, so the gateway keeps no standing key of theirs; it shows in
  `/v1/me/sessions` with `expires_at` and `via: "session"`.
- **OpenAI-shaped chat proxy.** `GET /v1/models` and `POST /v1/chat/completions`
  (streaming or not) go to the upstream with the relay's key. The default menu contains
  only `halogen-qwen3.8-flash-next`, including its image-input support for chat and GUI
  operation. Image and video generation require a provider and API key configured by
  the person on their device. A streamed turn that waits for one of the relay's
  `CHAT_CONCURRENCY` slots, or for a slow provider's first bytes, hears about it: chunks
  with `choices: []` and a `nanomuse: {phase: queued | working, ahead, waited_ms}` field
  while it queues, SSE comment lines while the provider thinks (0.31; shapes every
  client already skips; details in [docs/cloud.md](../docs/cloud.md#protocol)).
- **Ledger.** Every request is charged from the account's grant using the
  upstream's own `usage` (for streams, from the final usage chunk). Cheaper
  models are charged with a multiplier; pictures cost a flat amount. One
  lifetime allowance per account and a per-minute limit bound the damage of a
  leaked key.
- **Privacy by construction.** Phone numbers and e-mail addresses are looked
  up by HMAC-SHA256 hash, shown as a display hint (`138****8000`,
  `so***@example.com`), and kept AES-GCM-encrypted under a key derived from
  `CLOUD_SECRET` so the operator's page can tell accounts apart; the database
  file on its own reveals none of them. The database holds: hashed and
  encrypted identifier, key hashes, the keyed hash of a code still waiting to
  be used (six digits, so hashing it plainly would be a table an attacker could
  work through), video task ids, token counts per request, the profile below
  and, since 0.29, *the record* — each chat turn in full, the person's own data
  (see *The record*; `RECORD_TURNS=0` keeps none of it).
- **One look on every device.** `GET` / `PUT` / `DELETE /v1/me/profile` keep
  the agent's name and face for the account — the dragon, an emoji on a colour,
  or a face drawn in the avatar studio with its five small stills (WebP, 200 KB
  each at most) — last writer wins, with a `rev` that grows on every write.
  From 0.25 a write is partial: a field left out (or a blank name) keeps its
  stored value, so a rename sends the name alone and the face stays; an
  `avatar` other than `face` drops the stored pictures. Devices on the hub
  hear `{"type": "profile", "rev", "device"}` and fetch it; `?face=false`
  leaves the pictures out. Never a key or a message.
- **Which device connected what (0.17).** The same profile carries
  `connectors`: one entry per service a device connected — `id`, `label`,
  `url`, `auth` (`oauth` / `key` / `open`), `device`, `device_id`, `enabled`,
  `at` — so another device can say "connected on your Mac; sign in here to use
  it here". A device's `PUT` replaces only its own entries (those whose
  `device_id` is the writer's `device`) and leaves the other devices' as they
  were; `[]` clears its own; a write that carries only `connectors` leaves the
  name and look alone. At most 64 on an account (`400 too_many_connectors`).
  The credential never comes along: an entry with a key named like one
  (`token`, `secret`, `key`, `authorization`, `password`, at any depth) is
  refused with `400 no_secrets_in_profile` and nothing is stored.

Errors carry a stable `code` the app can turn into a sentence:

| status | code | meaning |
|---|---|---|
| 400 | `bad_identifier` | not a phone number or e-mail address |
| 400 | `phone_region` | a non-mainland number the relay cannot send a code to; e-mail works everywhere |
| 400 | `code_wrong` / `code_expired` | verification code |
| 400 | `channel_unsupported` | this relay's `CODE_SENDER` cannot reach that identifier (a phone number when the sender is e-mail only); the sentence says which |
| 429 | `code_too_often` | too many codes for the identifier in ten minutes or from the address in an hour; wait |
| 502 | `send_failed` | the mail provider refused or did not answer; the code was not sent |
| 400 | `bad_credentials` / `password_short` / `password_long` / `password_weak` / `password_wrong` / `password_required` | the password sign-in and the password form (`bad_credentials` is 401; `locked` is 429 after too many wrong passwords) |
| 404 | `no_session` | the sign-in to revoke no longer exists |
| 409 | `device_online` | `DELETE /v1/devices/{id}` (or the hub's `forget`) for a device that is connected right now; sign out on it first |
| 409 | `sync_off` | a `/v1/sync/*` call while the person has conversation sync turned off |
| 413 | `too_large` | the body is over `MAX_REQUEST_BYTES`, or a profile over its limit; `too_many_conversations` / `too_many_messages` are the sync push's own 413s |
| 413 | `quota_exceeded` | a file (`PUT /v1/blobs`, or a picture inlined in a turn) would take the account past `MEDIA_ACCOUNT_MAX_MB`; delete the record or send fewer pictures (0.30) |
| 507 | `store_full` | the relay's file store is at `MEDIA_MAX_GB` and the hourly trim has not run yet; the file was not taken, send it later (0.30) |
| 400 | `bad_request` | the body did not parse or a field is wrong; the message says which |
| 400 | `client_disconnected` | the app went away while its request body was still arriving; nothing reached the provider |
| 404 | `no_task` | an image or video job id the relay does not know (or not this account's) |
| 401 | `bad_key` | unknown or revoked key — the phone keeps the account's data aside for the next sign-in |
| 401 | `account_deleted` | the key's account was deleted (0.1.40; remembered for 90 days) — the phone may delete the account's data |
| 402 | `out_of_tokens` | grant used up — top up with the admin endpoint |
| 403 | `account_disabled` | the operator switched the account off: its keys answer `401 bad_key`, `POST /v1/auth/verify` answers this, and `/v1/auth/login` answers it only to the right password (a wrong one is `401 bad_credentials` as for any account). `POST /v1/auth/code` answers the same `204` as for anyone, counts towards the same limits and sends nothing, so a request there learns neither that the number has an account nor that it is off |
| 403 | `session_from_session` | `POST /v1/auth/session-key` was called with a session key; only a device's standing key may mint one, so a leaked short-lived key cannot renew itself |
| 404 | `model_not_offered` | not on the chat menu |
| 410 | `hosted_media_retired` | this relay no longer pays for image or video generation; configure an image/video provider and API key in the client |
| 400 | `no_secrets_in_profile` | a profile `connectors` entry carried a key named like a credential; nothing was stored |
| 400 | `too_many_connectors` | the write would leave more than 64 connectors on the account |
| 429 | `code_too_often` / `rate_limited` / `daily_cap` | (`daily_cap` only with the legacy token cap on; `rate_limited` is also what `POST /v1/turns` and `PUT /v1/blobs` answer past `RECORD_UPLOADS_PER_MINUTE`) |
| 429 | `too_many_in_flight` | `MAX_IN_FLIGHT` requests of the account are already under way; `retry_after` in the body |
| 400 | `content_rejected` | the provider's content check declined the words (a chat request or an image prompt); the provider's own line rides along under `upstream` |
| 400 | `upstream_400` | any other refusal of the request itself; the provider's message passed through (read from an object or, as Gemini's OpenAI-compatible endpoint sends it, from the first object of a list) |
| 502 | `upstream` / `upstream_auth` / `upstream_model` / `upstream_busy` / `upstream_<status>` | the provider failed; a plain sentence, the provider's line under `upstream` |
| 503 | `upstream_unconfigured` | the key for the requested chat or media provider is missing |
| 403 | `signup_closed` | the operator closed sign-ups (0.22, *Controls*); an identifier without an account gets this before any code is sent, existing accounts sign in as before |
| 503 | `service_paused` | the *Cloud service* switch is off (0.22): every call but `/healthz`, `/v1/config`, `/app` and `/v1/admin/*`; `paused: true` in the body |
| 503 | `sync_paused` | the *Conversation sync* switch is off (0.22): `/v1/sync/*` except `GET /v1/sync/state`, which answers with `paused: true` |
| 503 | `hub_paused` | the *Device hub* switch is off (0.22): `GET /v1/devices`; the hub socket itself closes with `4003 hub_paused` |
| 403 | `client_not_supported` | the *nanoMuse clients only* switch is on (0.28, the default) and the `User-Agent` is not one of the nanoMuse apps, the runtime or a browser: a pool worker, an SDK, a script, the unbranded upstream app; `client` in the body names the family. Members are answered from anything; the sign-in, the account and the allowance are untouched |

## Run it

Development, no external services — codes are printed to the log:

```bash
cd cloud
pip install -e ".[dev]"
UPSTREAM_KEY=sk-… python -m nanomuse_cloud --port 8787
# in another shell
curl -X POST localhost:8787/v1/auth/code -H 'Content-Type: application/json' -d '{"identifier":"13800138000"}'
# read the code from the server log, then
curl -X POST localhost:8787/v1/auth/verify -H 'Content-Type: application/json' -d '{"identifier":"13800138000","code":"123456","device":"curl"}'
```

Production, on any VPS with Docker — `scripts/self-host.sh` at the repository root asks the five questions, writes `.env` and runs this; [docs/self-hosting.md](../docs/self-hosting.md) is the guide, including how to point each app at your relay:

```bash
cp .env.example .env      # fill in CLOUD_DOMAIN, PUBLIC_BASE, CLOUD_SECRET, CLOUD_ADMIN_TOKEN, UPSTREAM_KEY, sender settings
docker compose up -d      # Caddy fetches the TLS certificate for CLOUD_DOMAIN
curl https://$CLOUD_DOMAIN/healthz
```

Without TLS, for this machine or a LAN — the relay alone on `127.0.0.1:8787`, codes in the log:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
RELAY_BIND=0.0.0.0 docker compose -f docker-compose.yml -f docker-compose.local.yml up -d   # reachable from the LAN
```

The image runs the relay as `relay`, uid 10001, not as root. Both compose files
bind-mount `./data` as the data directory (`CLOUD_DB`, the GeoIP file beside it),
and Docker creates a missing bind-mount source owned by root, which uid 10001
cannot write; so `data/` must belong to that uid on the host before the first
start, and once more when upgrading from an image that ran as root, whose
`cloud.db` is root's: `sudo chown -R 10001:10001 data` (or, without sudo,
`docker compose run --rm --no-deps --user 0 relay chown -R 10001:10001
/srv/nanomuse-cloud/data`). `scripts/self-host.sh` and the production
`deploy.sh` do this themselves; a relay that cannot write its data directory
stops at start with a sentence saying so instead of a traceback.

Back up `data/cloud.db` together with `CLOUD_SECRET`: the hashes are useless
without the secret, and the secret alone is useless without the database.

The visitor's address, what the per-address limits on codes and sign-ins key on
and what the operator's page shows, is the first hop of `X-Forwarded-For` when
the request came through a proxy on the same box or network (Caddy, a tailnet:
every loopback, private, link-local or shared address), else the socket's peer.
`TRUSTED_PROXIES`, when set, replaces that rule: only the addresses and networks
listed are believed, and a peer on the Docker network that is not among them
keeps its own address. A relay reached directly from the internet ignores the
header, so a request cannot name its own address.

Point the app at it: in nanoMuse, *Sign in — free* → enter a phone number or an e-mail → code.
The app stores the key in its encrypted preferences and sets up a provider
with `PUBLIC_BASE` as its base URL. Nothing else in the app changes; you can
add your own key next to it at any time.

## Settings

All configuration is environment variables; see [`.env.example`](.env.example)
for the full list. The ones that matter:

| variable | default | |
|---|---|---|
| `UPSTREAM_BASE` / `UPSTREAM_KEY` | `http://10.1.1.52:8000/v1` / empty | where chat goes: the local model server, no key needed |
| `CHAT_DEFAULTS` | `{"enable_thinking": false}` | merged into chat requests for fields the app did not set; for Qwen behind vLLM use `{"chat_template_kwargs":{"enable_thinking":false}}` |
| `SIGNUP_OPEN` | `1` | anyone may sign in; `0` = members only (a private relay). *Runtime setting* — see below |
| `ALLOWED_IDENTIFIERS` | empty | comma-separated numbers / addresses of the **members**: no spend limit |
| `ALLOWANCE_CNY` | 10 | yuan per non-member account **for its lifetime**, at the list prices below; 0 = no limit. *Runtime setting* |
| `SIGNUP_PER_DOMAIN_DAY` | 5 | new accounts one e-mail domain may open per rolling 24 hours when the domain is not a well-known mail provider (gmail.com, outlook.com, qq.com, 163.com and the like are never limited; phones never are); past it `POST /v1/auth/code` answers `429 domain_limited` and the timeline across accounts gets `signup.domain_limited` with the domain; 0 = off |
| `SIGNUP_DOMAIN_ALLOW` | empty | comma-separated domains that join the built-in list of unlimited ones, your company's or school's for instance |
| `SIGNUP_DOMAIN_BLOCK` | empty | comma-separated disposable mail domains blocked from opening new accounts in addition to the built-in blocklist (`DEFAULT_SIGNUP_DOMAIN_BLOCK`); `SIGNUP_DOMAIN_ALLOW` overrides an entry |
| `REPO_URL` | `https://github.com/knsew/nanoMuse` | the repository, in `/v1/config` |
| `IMPROVE_DEFAULT` | `0` | what *Help improve nanoMuse's AI models* (Data controls) starts as for accounts created from now on: `1` = on until the person turns it off, `0` = off until they turn it on; existing accounts keep their setting. The switch records nothing now (the collection was removed), so state that in your privacy policy |
| `PRIVACY_URL` | `https://github.com/knsew/nanoMuse` | the page the apps link from Data controls and the sign-in pages |
| `DAY_OFFSET_H` | 8 | the operator's reports group by local day, midnight UTC+8 (Beijing) |
| `USD_CNY` | 7.1 | for showing dollars next to yuan; display only |
| `SIGNUP_TOKENS` | 0 (no ceiling) | starter token grant per account, the older allowance |
| `DAILY_CAP_TOKENS` | 0 (off) | tokens per account per day |
| `PER_MINUTE_REQUESTS` | 30 | per account — what stops a runaway loop |
| `MAX_IN_FLIGHT` | 4 | requests of one account under way at the same time (0 = off); each holds a reservation against the allowance while it runs — see *Money* |
| `UPSTREAM_TIMEOUT_S` | 180 | how long one call to the provider may take (connecting has 20 s of its own); a reservation that outlives it by two minutes is dropped |
| `CHAT_CONCURRENCY` | 12 | chat requests at the provider at once across all accounts (0 = off); the rest wait at the relay, so a burst does not overload one shared deployment |
| `CHAT_RETRIES` | 2 | retries after an upstream 429 or 5xx received before any chat output starts (0 = off; capped at 5) |
| `CALLS_KEEP_DAYS` | 90 | how long the per-call telemetry rows (`calls`: timings, outcome, shape, platform; never content) are kept before the hourly sweep removes them (0.28) |
| `EVENTS_KEEP_DAYS` | 365 | how long the timelines' lines (`events`: sign-ins, devices joining, errors, refusals; never content) are kept before the hourly sweep removes them; the `account.deleted` lines and the ledger are never swept (0.29) |
| `RECORD_TURNS` | `1` | the record (0.29): every chat turn kept in full — the request as sent, the answer as given, pictures as files — for the account's devices and the operator. `0` keeps none of it and answers 404 to `POST /v1/turns` and `PUT /v1/blobs` |
| `RECORD_KEEP_DAYS` | 0 | how long a turn of the record stays; 0 = until the person deletes it (Data controls) or the account. The hourly sweep removes older turns and the files only they referenced |
| `MEDIA_DIR` | `<database dir>/blobs` | where the record's files live, one per distinct picture, audio or clip, named by SHA-256 (`<aa>/<sha256>`); back it up with the database if you want the pictures back |
| `MEDIA_MAX_GB` | 50 | the files' ceiling in all; past it the least recently used go first and their turns keep a reference to nothing (the words stay) |
| `BLOB_MAX_BYTES` | 32 MiB | the largest file one `PUT /v1/blobs/{sha256}` may carry (a clip of the face, a picture) |
| `MEDIA_ACCOUNT_MAX_MB` | 2048 | the most files one account may hold on the relay (its turns', its profile's and its uploads' together); a `PUT /v1/blobs` or an inlined picture past it answers 413 `quota_exceeded`, and once the store itself is at `MEDIA_MAX_GB` nothing more is taken until the hourly trim has run (507 `store_full`). 0 = no per-account cap (0.30) |
| `RECORD_UPLOADS_PER_MINUTE` | 120 | own-key turns and files one account may send up a minute (`POST /v1/turns`, `PUT /v1/blobs`); past it 429 `rate_limited`, and the app sends the rest later (0.30) |
| `CHAT_RETRY_BASE_S` | 0.5 | first retry pause in seconds; each later pause doubles |
| `TRUSTED_PROXIES` | empty | whose `X-Forwarded-For` to believe, comma-separated addresses or networks (`*` for every peer); empty = every loopback, private, link-local and shared address (Caddy on this box or the compose network, a tailnet). Set, it is the whole list: a non-global peer not on it is not believed either |
| `PASSWORD_MIN_LEN` | 8 | the shortest password the relay accepts (`400 password_short` below it) |
| `PASSWORD_MAX_ATTEMPTS`, `LOCKOUT_S` | 5, 900 | wrong passwords before an account is locked for that long (a code still works) |
| `PASSWORD_RESET_WINDOW_S` | 1800 | after a sign-in with a code, how long a new password may be set without the old one (the way back in when it is forgotten) |
| `LOGIN_FAIL_PER_IP_HOUR` | 30 | wrong passwords from one network address per hour across all accounts — a list of numbers tried once each never trips the per-account lock, this does (0.12); 0 = off |
| `CODE_TTL_S`, `CODE_MAX_ATTEMPTS` | 600, 5 | how long a sign-in code is good for, and how many wrong guesses it survives |
| `CODE_PER_IDENTIFIER_10M`, `CODE_PER_IP_HOUR` | 3, 10 | codes one number or address may request per ten minutes, and one network address per hour; past either the request is answered `429 code_too_often` |
| `CODE_SENDER` | `log` | `log` or `smtp` (e-mail only; see *Sending codes*) |
| `CLOUD_MODELS` | `halogen-qwen3.8-flash-next` | JSON list to replace the default menu and lanes (`for`, `recommended_for`); media kinds take effect only on a self-hosted relay with `CLOUD_MEDIA_ENABLED=1` |
| `CLOUD_MEDIA_ENABLED` | `0` | `1` lists non-chat models in `/v1/models` and lets the budget check pass them; the relay itself generates no pictures or video |
| `CLOUD_MODEL_ALIASES` | built-in aliases only | JSON object mapping old or saved model ids to ids in `CLOUD_MODELS`, useful when changing provider |
| `CLOUD_ANY_MODEL_MEMBERS` | `1` | members may name any model of the provider's for its kind (chat, image, video) — see below; `0` = the menu only |
| `CLOUD_CATALOG` | `1` | list the usable models under the operator's key after the menu in a member's `/v1/models`, read from the provider's own `/models` (0.10) — see below; `0` = the menu only, a member types an id |
| `CLOUD_CATALOG_TTL_S` | `3600` | how long that list is kept before the provider is asked again |
| `CLOUD_CATALOG_PROBE` | `1` | ask each chat model on that list, once, whether it answers and whether it reads a picture (0.11) — see below; `0` = go by the names |
| `CLOUD_CATALOG_PROBE_TTL_S` | 7 days | how long a model's answers stand before it is asked again |
| `HUB_ENABLED` | `true` | the devices hub at `/v1/hub` and the web console at `/app` ([docs/hub.md](../docs/hub.md)) |
| `HUB_FRAME_LIMIT` | 16 MB | largest hub frame (files and screenshots travel inside frames); one socket may also send at most 60 frames and 8 MB a second sustained (twice that in a burst); over it frames are dropped with one `rate_limited` error a second, and a socket that keeps flooding is closed with 4008 (0.13); one address may open 60 sockets a minute and one account may have 32 devices connected at once, over either the socket is closed with 4008 `too_many_connections` before any key is checked (0.30) |
| `MAX_REQUEST_BYTES` | 16 MB (0.20; was 6) | largest REST request body — a chat request with a few screenshots, a sync push of 200 messages; over it the answer is 413 `too_large`, *Request body is N MB; this relay accepts up to M MB*. A declared `Content-Length` over the limit is refused before a byte is read, and a body that grows past it is refused as it arrives, so the relay never buffers more than the limit for one request. The Caddy site (`cloud/Caddyfile`) caps bodies at `request_body max_size 20MB`, just above 16 MiB, so a legitimate payload always reaches the relay's own 413 message and nothing larger reaches the relay at all; raise both together if you raise this |
| `ADMIN_EMAIL` | empty | where a threshold rule's *notify* goes (0.22, *Controls*), through the `SMTP_*` settings; empty = the rule writes its audit line and the log says there was nobody to tell |
| `NANOMUSE_CLOUD_BASE` | `PUBLIC_BASE`, else `http://127.0.0.1:8787` | read by the admin CLI only (`nanomuse-cloud admin …`, below): the address of the relay it talks to when `--base` is not given; the relay itself does not read it |

The default menu contains one model: `halogen-qwen3.8-flash-next`. It serves both
chat and the hands and accepts image input, so screenshots and pictures in a
conversation continue to work. It does not generate pictures or video.
`/v1/images/*`, video submission/upload routes, and media estimates answer
`410 hosted_media_retired` before any upstream call or allowance charge.

**Lanes (0.17).** Every chat model says what it is `for` — `chat`, `gui` or
both — and `recommended_for` names the lane(s) it is the default pick in; both
ride in each entry's `nanomuse` block of `/v1/models`, and the apps' two
pickers (the chat model, the hands model) filter on them and take the
recommended one of each lane as the default. Picture and clip models are for
neither (`"for": []`; their `kind` says what they do). On the shipped menu
`halogen-qwen3.8-flash-next` is `for: ["chat", "gui"]` and recommended for both. In
`CLOUD_MODELS` the fields are `"for"` and `"recommended_for"` (lists of lane
names; `for` left out means `["chat"]`; a `recommended_for` needs
`"recommended": true`); the relay logs a line at start when a lane has no
recommended model or more than one. A thinking model's `enable_thinking` and
the `reasoning_content` an app sends back in the history go upstream as they
are, the reasoning in the reply (whole or as stream deltas) comes back
untouched, and reasoning tokens are counted as completion tokens whichever
way the provider reports them (`completion_tokens_details.reasoning_tokens`
inside the figure, or DashScope's `output_tokens_details` apart from it).

Any OpenAI-compatible upstream works for chat. The legacy image and video
endpoints assume DashScope and are disabled by default; a self-hosted relay
can opt in with `CLOUD_MEDIA_ENABLED=1` and its own provider key. Video is
then relayed under DashScope's own paths
(`/api/v1/services/aigc/video-generation/video-synthesis`, `/api/v1/tasks/{id}`,
`/api/v1/uploads`), so the app's video code only needs to point its host at
the relay; a task can be polled by the account that created it only, and the
relay polls on the account's behalf when the app does not (*Money*, below).

### Runtime settings (0.15)

Two of the values above, `ALLOWANCE_CNY` and
`SIGNUP_OPEN`, can be changed **while the relay runs**, from the operator's
page (*Settings › Runtime*) or `POST /v1/admin/settings` (`{"allowance_cny":
20}`; `null` or `""` puts a value back on its environment default). The
change is kept in the database (`settings` table), survives a restart, and
takes effect on the next request: a new sign-up gets the new allowance, and
`/v1/config` — the public endpoint
the apps read for the figures they print ("¥20 of use to start") — says so
at once, with a minute's cache. **Nobody has to
update an app or do anything**: the amounts are the relay's, the apps only
display them.

Raising the allowance does not by itself touch the accounts that exist: each
account remembers the allowance it was created under (`accounts.allowance_uy`,
seeded with the value of the day for accounts from before 0.15). The page
counts how many non-member accounts are below the current figure and
**Apply to existing accounts** (`POST /v1/admin/allowance/apply`) credits each
of them the difference as a ledger row (`from: allowance`), so a raise from
¥10 to ¥20 gives everyone who had ¥10 another ¥10 — and no one twice.
Lowering the figure only applies to accounts created from then on; the
allowance machinery never shrinks a pool. **Credit everyone** (`POST
/v1/admin/credit-all`, `{"cny": 5, "note": "..."}`, −¥100…¥100) is the one-off
present — or, negative, the one-off claw-back: every non-member, enabled
account gets the amount, once, as `from: operator`.

**Set a pool to any figure** (0.16). `POST /v1/admin/pool` with `{"account_id"
| "identifier", ...}` and exactly one of `left_cny` (what should be left right
now — the pool becomes what is spent plus that), `grant_cny` (the lifetime
total) or `delta_cny` (a difference, negative takes away), plus an optional
`note`; the pool never goes below zero, what is spent stays spent. `POST
/v1/admin/pool/batch` does the same for `account_ids: [...]` (the filtered list
on the People view — *Set the pool for these N*) or for `all: true` (every
limited account — *Set everyone's pool* under Settings › Runtime; members and
disabled accounts are left out). Each account gets a ledger row (`credit_uy`
signed, `set` the new total) and a `pool.set` line on its timeline, so the
person sees the adjustment on their account page.

`GET /v1/admin/settings` returns the values in force, the environment's,
which are overridden, and the count below the allowance; `GET /v1/config`
(no key) returns `version`, `signup_open`, `allowance_cny` / `allowance_usd`,
`usd_cny`, `privacy_url`, `repo_url`, `improve_default`, and the two figures the
sign-in and password forms print, `code_ttl_s` and `password_min_len`, so the web
console says how long a code is good for and how short a password may be in the
relay's own numbers.

### Controls (0.22)

Six switches the operator flips without a restart or a deploy, from the
*Controls* page of the console, the command line or `POST
/v1/admin/controls/{key}`: `free_allowance`, `signups`, `cloud_service`,
`sync`, `hub` and, since 0.28, `apps_only`. Each is on by default, kept in the
`controls` table so a restart keeps it, and applies to the next request. What
each one refuses when off is in the error table above and, from the app's
side, in [docs/cloud.md › Controls](../docs/cloud.md#controls); the console
says the same thing in the row and again in the confirmation before it flips.
Turning `cloud_service` or `hub` off closes every open hub socket (`4003
hub_paused`) and the answer says how many. The five that pause a part of the
service are listed under `paused` in `/healthz`, `/v1/config`, `/v1/me` and
`GET /v1/admin/settings` while they are off.

`apps_only` is the one switch that refuses while it is *on*: the models answer
the nanoMuse apps, the desktop app, the Python runtime and the web console in
a browser, and turn away everything else on a nanoMuse key with `403
client_not_supported` (a pool worker, an SDK, a script, the unbranded upstream
app; `client` in the body names the family, the message says to use a key of
one's own in other software). It reads the `User-Agent`, so it stops honest
programs and not impostors; the runtime of 1.0.6 and earlier, which called the
models with the OpenAI SDK's own string (`AsyncOpenAI/Python`), is answered as
the runtime it is, and so is a script on that SDK; members are exempt; the
sign-in, the account page, sync and the hub do not look at it. A refusal is a `calls` row (`refused`,
`client_not_supported`) and a `client.refused` line on the account's timeline,
costs nothing and never counts as a budget refusal; the *Non-app traffic* page
of the console lists what was turned away, by family. Off, any client with a
valid key is answered, as before 0.28.

Every change is a row in `control_audit` — who (`X-Admin-Actor` from the
console's *Your name* field or the CLI's `--actor`, else `console` / `cli:<user>`
/ `rule:<id>`), when, which switch or rule, the new state and the note — and
a `control.switch` / `control.rule` line on the activity timeline.

**Thresholds** (`control_rules`): *when the account count reaches N, do one
action* — `close_signups`, `pause_allowance`, `pause_sync` or `notify`. Rules
are evaluated when an account is created and once a minute by a background
task; a rule fires once and remembers the count and time it fired at
(`last_fired_at`, `fired_accounts`); *re-arm* on the page (`PUT …/rules/{id}`
with `{"rearm": true}`) or a changed threshold lets it fire again; `enabled:
false` keeps the rule without evaluating it. Firing writes the audit line and
the timeline line; `notify` also sends an e-mail to `ADMIN_EMAIL` through the
`SMTP_*` settings — counts and the relay's own address only, nothing about any
person — and the audit says whether it went. *Send a test notice* on the page
(`POST /v1/admin/controls/notify-test`) checks the mail settings.

```
GET    /v1/admin/controls                       → {switches: {key: {enabled, updated_at, actor, note}}, rules, accounts_total, next_threshold, notify_configured, admin_email_set, audit}
POST   /v1/admin/controls/{key}                 {enabled, actor?, note?}  → the same, plus sockets_closed
GET    /v1/admin/controls/rules                 → {rules: [{id, threshold, action, enabled, note, created_at, last_fired_at, fired_accounts}]}
POST   /v1/admin/controls/rules                 {threshold, action, note?, enabled?}  → the rule
PUT    /v1/admin/controls/rules/{id}            {threshold?, action?, enabled?, note?, rearm?}  → the rule
DELETE /v1/admin/controls/rules/{id}            → 204
POST   /v1/admin/controls/evaluate              → {fired: [...], accounts_total}   check the rules now
POST   /v1/admin/controls/notify-test           → {sent, configured}
GET    /v1/admin/controls/audit?limit=200       → {audit: [{id, ts, actor, ip, action, target, detail}]}
```

### Star asks

The apps used to ask for a star on GitHub on a schedule the relay set. That
is gone: `GET /v1/nudges` (no key) always answers
`{"star": {"enabled": false}}`, and `/v1/me` carries the same object under
`nudges`, so older apps simply never ask. There is no policy to set and no
admin route for it.

### Money

Every request is priced in yuan at the provider's Beijing list prices (set per
model: `price_in` / `price_out` per million tokens, `price_in_idle` /
`price_out_idle` for a model priced by the hour with `idle_hours` as
`[start, end]` in Beijing time, `cached_in_rate` for the share of the input
price a cached prompt token costs, `price_image` and `price_image_2k` per
picture out and `price_image_in` per picture sent in, `price_second` per second
of video at the base resolution and `price_second_res` as `{"720P": 0.2, ...}`
above it) and stored in the ledger next to the token count; a chat is priced
at the hour its reply comes in, with the provider's `cached_tokens` at the
cached rate, and the ledger line's `extra` says when either applied. A non-member account has one pool for
its lifetime, `ALLOWANCE_CNY` (¥10 by default, adjustable at runtime), grown by the operator's credit. Only chat/vision/GUI
requests spend this pool; picture and clip generation returns
`hosted_media_retired` before budget or upstream work.
Members — the identifiers in `ALLOWED_IDENTIFIERS`, or any account the
operator marks on the admin page — have no limit, and (with
`CLOUD_ANY_MODEL_MEMBERS=1`, the default) may name **any model the provider has
under the operator's key**, not only the menu's: the id goes upstream as typed,
as long as it is a chat model at `/v1/chat/completions`. Disabling hosted media
also applies to members. `/v1/models`
says `nanomuse.any_model` for the account, `/v1/me` the same under `account`,
and `GET /v1/models/<id>?kind=chat` checks a typed id (the answer's `listed` is
false and `priced_as` names the menu model whose prices stand in for it in the
ledger — the dearest of its kind, so the operator's page errs high). It is how
a member tries a model before it goes on the menu. From 0.10 a member does not
have to know the id: with `CLOUD_CATALOG=1` (the default) the relay reads the
provider's own `/models` under the operator's key (once an hour,
`CLOUD_CATALOG_TTL_S`), sorts the ids by their shape into chat and picture
models — the spoken, heard, embedding and rerank ones are left out, and a
video model is not on the compatible list (`catalog.py`) — and lists them in
the member's `/v1/models` after the menu (chat only while hosted media is
disabled), each with `catalog: true`, `listed:
false`, `priced_as` and `vision` (whether the chat model reads pictures); the
answer's `nanomuse.catalog` says how many and the provider's error if the list
could not be refreshed (the last one stands). The apps' pickers then show the
menu and *More models on your account* as two groups, and a guest sees the
menu alone. Names are a guess, and a wrong guess costs a person a feature —
DeepSeek V4 on Model Studio reads pictures, nothing in its name says so, and
an id the provider has retired still appears on `/models` — so from 0.11 the
relay checks (`CLOUD_CATALOG_PROBE=1`): after the list is read, each chat
model is asked, in the background and three at a time, to reply with one
word and then to name the colour of a small magenta square; a model the provider
refuses is left off the list, `vision` is what the model answered, and
`verified: true` marks an entry the probes have confirmed (the name's guess
stands while a probe is pending). The answers are kept in the database
(`model_probes`) for `CLOUD_CATALOG_PROBE_TTL_S`, a week by default, so a
restart asks nothing again; `/v1/admin/catalog` shows the whole of it — which
models answer, which see, which were refused and with what words — and the
operator's page has a *Model catalog* panel. The relay also reads what a
request says about reasoning before it applies `CHAT_DEFAULTS`: an app that
sends `reasoning_effort` (or `thinking`, `thinking_budget`) asked for
thinking, and the shipped `enable_thinking: false` would make Model Studio
refuse the pair, so the default follows the request. The same rule applies to
`chat_template_kwargs.enable_thinking`, the switch used by Qwen chat templates
behind vLLM. `/v1/me` carries a `spend`
block (`total`, `grant`, `left`, `unlimited`, `warn` at 80 %, `usd_cny`,
`total_usd`, `grant_usd`, `left_usd`, `today`; the 0.4 names `daily_cap` /
`left_today` / `resets_at` = 0 for one more version) and each model in
`/v1/models` carries its `nanomuse.price_cny`, so the apps show what was spent
in both currencies. With the legacy token cap on (`SIGNUP_TOKENS` above 0), a spent grant is refused as 402 `out_of_tokens`; sign-in and
the hub are never gated, only the model routes. The admin page shows spend per
account and per day (`DAY_OFFSET_H`) in ¥ and $.

**Where the person is (0.17).** `/v1/me` (and the sign-in answers) carry
`region`: `cn` for an account opened with a mainland China phone number,
`unknown` for everyone else. There is no geo lookup: the region is read from
the number's country code alone, and nothing is sent anywhere for it.

`SIGNUP_TOKENS=0` (the default) runs the relay without a token ceiling: usage
is metered and shown, nothing is refused for lack of tokens (`/v1/me` says
`"unlimited": true` and the apps show 「不限」). `DAILY_CAP_TOKENS=0` and
`PER_MINUTE_REQUESTS=0` switch those two checks off in the same way.

**Reserve, then settle (0.13).** Requests started together used to pass the
allowance check one by one and overshoot it together. Now each request is
*reserved* while it runs and *settled* when it is over: a chat at a typical turn's worth of its model (6 000 prompt
and 1 500 completion tokens — the ledger gets the real figure when the reply is
in). The check counts what is held: a chat starts while anything is left beyond it. At most `MAX_IN_FLIGHT`
requests of one account run at once; the one over that is told so
(`429 too_many_in_flight`, `retry_after`) rather than queued. Reservations live
in memory and lapse on their own after the upstream timeout, so a client that
vanished before its stream began cannot hold a slot for good.

### Operator's page

`/app/admin/` asks for `CLOUD_ADMIN_TOKEN` (kept in the tab's sessionStorage)
and is the dashboard from `/v1/admin/overview`: how many accounts (with a
password, members, disabled), who was active today and over the period,
what it cost today / this week / over 7, 30 or 90 days split by kind (chat,
pictures, video) and by model, spend by day as stacked bars, the top
spenders, today's signals (sign-ins, failures, budget refusals, upstream
errors) and the timeline across accounts with a kind filter. The
accounts table showed the masked hint until 0.22 and now the number or
address itself (below); opening one account (`/v1/admin/accounts/{id}`)
shows its spend by kind / model / day, sign-ins (device names,
revoked ones too), remembered devices with presence, every request and its
whole timeline — page by page to the first line
(`/v1/admin/accounts/{id}/ledger` and `/events`, `?before=&limit=`) — with
the grant / member / disable / delete buttons. From relay 0.10 the relay
records the network address and the client (`User-Agent`) with each sign-in,
request, event and device, and the account's first / last address and last
client: the accounts table has a *Client / IP* column, the drawer an
*Addresses / IP* section (how often, first, last, platforms) and the address
on every row, and an address opens the accounts seen from it
(`/v1/admin/address?ip=`). The text of a chat is on the page only for
accounts that kept turns in the past (the switch is gone; nothing new is kept),
and only the training
view of it (below); every account that kept turns is in the *By account*
table of the Data controls panel, and its conversations are read in full in
the drawer — each turn expands to every message and tool call — with an
export of that account's turns (`/v1/admin/samples/export?account_id=`).
Identifiers are kept AES-GCM-encrypted with a key derived from
`CLOUD_SECRET`. A person can also remove themselves: `POST /v1/auth/delete`
with their key deletes the account, its keys, ledger and devices.

One more panel comes from `/v1/admin/series`: the
relay's own numbers by day (sign-ins, new and active accounts,
data switches turned on, calls, refusals, upstream errors, and the
provider's content-check refusals, counted apart since they say nothing about the provider),
and the remembered devices by kind and system.

From 0.14 the page is pages. A side navigation holds *Overview*, *People*,
*Money*, *Activity*, *Data controls*, *Models*,
*Health* and *Settings*; the view and its filters live in the hash
(`#people?country=中国&province=广东省&status=member&sort=spent`), so a view
is a bookmark and a link the operator can paste to themselves, and each
view fetches its own numbers the first time it is opened (the period
selector applies to all of them). *Overview* keeps the headline tiles, a
health strip, spend by day, the top regions and spenders and the latest
events, each panel linking to its page. *People* is the accounts filtered:
by region,
sign-up channel, status (member, disabled, locked, with or without a
password, *Help improve* on, pool used up, a device online, new this
period), last activity (24 h, 7 d, 30 d, older, never), lifetime spend
bucket and last client, plus a search over the hint / id / address — and the
charts above the table are the same filters drawn: sign-ups by day, where
they are (click a country to see its provinces, a province its cities),
spend buckets, activity, channel and client, each bar a filter that one
click applies and a second removes. The table sorts by any column and
exports the current selection as CSV (with the identifiers since 0.22, as
the table shows them).
*Money* has the kind chips (chat, pictures, video, calls) and
a share bar over the model table; *Activity* searches the timeline; *Models*
searches the catalog; *Health* is `/v1/admin/health` on the page (in-flight
requests, hub connections, the last hour, the database, the upstream key),
refreshed every 30 s.

From 0.22 the console shows accounts in full: the phone number or e-mail
address stands beside the hint in the *People* table, the top spenders, the
latest events and the account drawer (`/v1/admin/overview` and
`/v1/admin/accounts` decrypt them for the console's answer; the People CSV
carries them too — the file is the operator's). Everything that leaves the
console — the server log, the e-mails, `/v1/admin/health`, the public pages —
still carries the masked hint only. Two pages are new: *Controls* (the five
switches, the threshold rules and the audit log — [above](#controls-022); the
first line of *Overview* says which switches are off and the next threshold)
and *Stats*, from `GET /v1/admin/stats?days=` — one registry in
`nanomuse_cloud/stats.py` defines every figure once, with a sentence saying how
it is computed (the ⓘ on each panel) in English and Chinese: accounts per UTC
day (total, new, active by any call and by model call, sign-ins, failures,
deletions), by sign-in method and by origin (country code or e-mail domain),
devices per account and by platform, model calls and tokens per day, by model
and the top 20 accounts, how far the limited accounts' pools are used, sync
pushes / pulls / messages per day, errors per day by code, API calls per day
by group, and the relay itself (version, started at,
uptime, database size, hub sockets, what is paused). Every panel has a table
and a CSV (`GET /v1/admin/stats/{id}.csv?days=`); days are UTC with the
operator's local date beside each. The counters behind the per-day figures
(active accounts, API calls, errors, sync traffic) live in the `daily` table,
so a restart loses nothing. From 0.26 the console searches and filters further. On *People* the search
has a row of its own above the filters and reaches the number or e-mail, the
account id (anywhere in it), both addresses, device names, the client and its
version and the region; several
words, space-separated, must all match. Three filters joined the row below:
*Joined* (24 h, 7 d, 30 d, longer ago) and *Version* (the client versions seen, with counts).
`/` on any page puts the cursor in that page's search box, on a page without
one it opens *People*; the CSV gained `version`.
The account drawer's statement and timeline each have kind chips (chat,
pictures, video, calls, credits; sign-ins, refusals, errors, content checks,
calls, passwords, credits) and a text box (model or note; detail or
kind name) sent to the relay as `?kind=&q=`, so the count is of what matches
and the filter holds across pages; an account that never made a request gets
one line instead of three empty tables. Opening another page closes the
drawer, which used to stay over it. *Activity* reads on past its first page
(`?before=`, 200 rows a time, the kind filter kept); the by-account table of
*Data controls* has a search box; the
*Stats* page opens with every metric's name as a jump to its panel; a KPI's
second figure wraps under the first instead of being cut off.

From 0.28 the console is rebuilt as a small React application (`cloud/console`,
Vite + TypeScript; `npm run build` writes `nanomuse_cloud/console/admin/`
under fixed names, and CI fails when the committed build differs from the
sources). Every answer is cached by URL and refreshed in the background, so a
page the operator has seen opens at once; the period (7 to 365 days), the
language and the theme are remembered; every filter, tab and open drawer lives
in the hash. The navigation is in three groups. **Insight**: *Overview*;
*Live* (`GET /v1/admin/live`, every 3 s): the provider gate (`CHAT_CONCURRENCY`
slots in use, how many wait), the requests under way with their phase (queued,
awaiting the first token, streaming) and age, the last hour by minute with
outcomes, latency percentiles and output tokens, the newest finished calls,
and a verdict (smooth, busy, saturated) from the last ten minutes' queue share
and queue P95; *Usage* (`/v1/admin/analytics/usage`): the day × model matrix
by requests, cost, tokens, cached tokens or accounts, how much of the prompts
the provider's cache served (overall and per model, from the ledger; and for
the turns people ran on keys of their own and sent up, per provider and
model, from the figures the relay keeps beside the record — `own_figures`,
one small row per own turn, written with the turn and filled in for older
turns by a paced backfill after the 0.31.1 start; the console's
`own_keys_pending` says when that is still running), and a click on a day
opens it
(`/v1/admin/analytics/day`): hours × models, platforms, outcomes by model,
latency by hour, the provider's error reasons, the top accounts; *Platforms*
(`/v1/admin/analytics/platforms`): Android, iOS, desktop, the runtime, the
browser and the rest side by side with versions, call telemetry, the hub's
devices, and the models people configured themselves (from the hello's
`device.llm`); *Retention* (`/v1/admin/analytics/cohorts`): how much of the free
allowance is used, how fast it ran out, what the exhausted did next, who likely
runs their own key (synced model replies without a cloud request), and weekly
retention by sign-up week; *Screen operation* (`/v1/admin/analytics/hands`):
turns that carried the operator's tools, by day, platform, tool and account;
*Data quality* (`/v1/admin/analytics/quality`): the kept turns' shape, trivial
and repeated prompts, accounts scored into high, medium and low tiers, likely
scripted accounts, the prompts repeated most (as hashes); *Non-app traffic*
(`/v1/admin/analytics/abuse`): client families that are not the apps, their
share of requests and cost, the heaviest accounts on them, addresses shared by
many accounts. **Operations**: *People* (filters, charts, batch pool changes,
CSV, the account drawer with every action), *Activity*,
*Data and sync*. **System**: *Health*, *Model
catalog*, *Statistics*, *Controls*, *Settings*. Every string is in 简体中文 and
English (`src/lib/locales.ts`; a test keeps the two tables in step), and the
operator's name typed at the top goes out as `X-Admin-Actor`.

**Every operator route**, each under `X-Admin-Token` (401 `admin` without it;
`X-Admin-Actor` names who did it for the audit log, else `console`). An account
is named by `account_id` or by `identifier` (the number or address itself):

```
GET    /v1/admin/health                          → {ok, version, in_flight, hub, last_hour, db, upstream_key, problems[]}; aggregates only
GET    /v1/admin/overview        ?days=30        → the dashboard: accounts, today / week / period by kind and model, signals, events
GET    /v1/admin/accounts                        → {accounts: [{id, identifier, hint, channel, member, disabled, …, devices[]}], settings}
GET    /v1/admin/accounts/{id}   ?days=30        → one account in full: usage by kind / model / day, sign-ins, devices, addresses, timeline
GET    /v1/admin/accounts/{id}/ledger ?limit=&before=&kind=a,b&q=   → the statement, page by page (before = the last id shown); kind and q (model or note) narrow it and its total (0.26)
GET    /v1/admin/accounts/{id}/events ?limit=&before=&kind=a,b&q=   → the timeline, page by page; kind and q (detail or kind name) the same way
GET    /v1/admin/address         ?ip=            → the accounts seen from one address
GET    /v1/admin/usage           ?days=14        → charged tokens and yuan per day and kind
GET    /v1/admin/events          ?limit=200&kind=a,b&before=   → the timeline across accounts (never message content); before = the last id shown (0.26)
GET    /v1/admin/series          ?days=30        → by day: sign-ins, new and active accounts, data switches; devices by kind and system
GET    /v1/admin/data            ?days=30        → Data controls: switches on, kept turns by day / model / app / account, the newest turns
GET    /v1/admin/samples         ?account_id=&limit=&since=&before=&before_id=   → kept turns
GET    /v1/admin/samples/export  ?since=&account_id=    → the same as JSON lines, without account ids or addresses
GET    /v1/admin/turns           ?account_id=&limit=&since=&before=&before_id=&conversation=   → the record (0.29): whole turns, newest first
GET    /v1/admin/turns/export    ?since=&account_id=    → the record as JSON lines, account ids included (it is the account's data, not the training set)
GET    /v1/admin/record          ?days=30        → what the record holds: calls and questions in all and per day, calls per question, the store against MEDIA_MAX_GB
GET    /v1/admin/record/turns    ?account_id=&conversation=&platform=&run=&source=&model=&script=&media=&hands=&since=&limit=&before=&before_key=   → the record question by question (0.31): each laid out once from its calls
GET    /v1/admin/turns/{id}                      → one call of the record in full
GET    /v1/admin/blobs/{sha256}                  → one file of the record
GET    /v1/admin/client-events   ?account_id=&kind=&since=&limit=&before=   → what the apps reported around turns (0.31): Sentinel decisions, approvals, stop reasons, thumbs; codes only
GET    /v1/admin/client-events/export  ?since=&account_id=   → the same as JSON lines, with the subject pseudonym and no account id
GET    /v1/admin/client-events/summary ?days=30  → by day and kind, and the split of each kind's codes
GET    /v1/admin/deletions       ?since=         → the deletions ledger (0.31): the subject of every account that deleted its data or itself since
GET    /v1/admin/sync                            → conversation sync in aggregate (never a text)
GET    /v1/admin/catalog                         → the provider's models under the operator's key: which answer, which see, which were refused
GET    /v1/admin/stats           ?days=30        → every figure of the Stats page with how it is computed
GET    /v1/admin/stats/{id}      ?days=30        → one figure as rows;  GET /v1/admin/stats/{id}.csv the same as CSV
GET    /v1/admin/live                            → the model endpoint now (0.28): gate, requests under way, the last hour by minute, latency percentiles, newest calls
GET    /v1/admin/analytics/usage ?days=30        → day × model: requests, tokens, cost, accounts
GET    /v1/admin/analytics/day   ?day=<utc ts>   → one day: hours × models, platforms, outcomes, latency, error reasons, top accounts
GET    /v1/admin/analytics/platforms ?days=30    → the apps side by side, versions, call telemetry, hub devices, reported own models
GET    /v1/admin/analytics/cohorts ?days=30      → the free pool, exhaustion, what came after, own-key accounts, weekly retention
GET    /v1/admin/analytics/hands ?days=30        → screen-operation turns by day, platform, tool and account
GET    /v1/admin/analytics/quality ?days=30      → the kept turns' shape, accounts scored and tiered, repeated prompts (hashes)
GET    /v1/admin/analytics/abuse ?days=30        → client families that are not the apps, sign-ins by client, shared addresses
GET    /v1/admin/settings                        → the runtime settings in force, the environment's, which are overridden
POST   /v1/admin/settings        {allowance_cny?, signup_open?}   → the same; null puts a value back on its default
POST   /v1/admin/allowance/apply                 → {accounts, allowance_cny}; the raised allowance reaches the accounts below it
POST   /v1/admin/grant           {account_id | identifier, tokens}                   → the account; the older token grant
POST   /v1/admin/credit          {account_id | identifier, cny, note?}               → the account with its pool fields
POST   /v1/admin/credit-all      {cny, note?}    → {accounts, cny}
POST   /v1/admin/pool            {account_id | identifier, left_cny | grant_cny | delta_cny, note?}   → the account with its pool fields
POST   /v1/admin/pool/batch      {account_ids[] | all, left_cny | grant_cny | delta_cny, note?}      → {accounts, <mode>}
POST   /v1/admin/disable         {account_id | identifier, disabled?}  → 204; true by default; the account's hub sockets are closed
POST   /v1/admin/unlimited       {account_id | identifier, unlimited?} → 204; true by default
POST   /v1/admin/delete          {account_id | identifier}             → 204; the same deletion as the person's own
GET    /v1/admin/controls …                      → the switches, the rules and the audit log (the Controls section above)
```

Every operator action on an account or on money writes a line in the audit log
(`control_audit`: who, when, from where, which action, which account by the
first eight characters of its id) and, where the account keeps a timeline, a
line on it too: `account.disabled` / `account.enabled`, `member.set` /
`member.unset`, `grant.tokens`, `credit.granted`, `pool.set`. A deletion, the
person's own or the operator's, writes `account.deleted` across accounts (the
id's first eight characters and who did it), which the Stats page counts as a
deletion; the account's own timeline goes with the account.

### Web console

`/app/` is the person's own page in the same design as the phone app: sign in
with a code or a password, the devices of the account with their Muses to
talk to, and an account sheet (`/v1/me`) with the allowance, usage by kind
and by model, the sign-ins with a way to revoke each, the recent activity,
set / change / remove the password, sign out here or everywhere.

### Data controls (0.9)

*Help improve nanoMuse's AI models* used to be one switch per account: with
it on, each chat turn through the relay was kept in `samples`, **the
training view only**: what the person wrote, what the model answered and the
tool calls it chose, plus the model id, token counts and the app and language
headers. Never the system prompt (the person's memory, SOUL and
instructions), never what a tool returned (their files, their screen, what
another app showed), never a picture, a clip or a voice note (a marker stands
where one was), and never next to who they are: a sample carries the account
id only and the export (`GET /v1/admin/samples/export`, JSON lines) leaves
even that out.

The switch is gone: no new turn is kept, `/v1/me.contribute` always says
`on: false`, and there is no `POST /v1/me/contribute`. What was kept before
stays readable: the operator's page has a *Data controls* panel from `GET
/v1/admin/data` (turns kept by day, model and app, the newest turns, and the
export), and the person still deletes what was kept with `DELETE
/v1/me/samples`; deleting the account deletes them too.

## Operating

```bash
# is it well? aggregates only (0.13): requests under way, the hub's counters, the last
# hour's requests / upstream errors / content-check refusals / refusals / sign-ins, the
# database, `problems` (twenty upstream errors in an hour is one; a content check declining
# words is not)
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" https://$CLOUD_DOMAIN/v1/admin/health
# who signed up (with the phone numbers and addresses since 0.22 — the console's answer)
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" https://$CLOUD_DOMAIN/v1/admin/accounts
# top up someone by phone/e-mail or by account id
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"identifier":"13800138000","tokens":500000}' https://$CLOUD_DOMAIN/v1/admin/grant
# thank a contributor: ¥10 more in their pool
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"identifier":"dev@example.com","cny":10,"note":"PR #12"}' https://$CLOUD_DOMAIN/v1/admin/credit
# switch an abusive account off
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"identifier":"13800138000","disabled":true}' https://$CLOUD_DOMAIN/v1/admin/disable
# raise the starter allowance for everyone from now on (0.15) — no app update needed …
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"allowance_cny":20}' https://$CLOUD_DOMAIN/v1/admin/settings
# … and give the accounts that exist the difference, once
curl -X POST -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" https://$CLOUD_DOMAIN/v1/admin/allowance/apply
# a one-off present to every non-member account
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"cny":5,"note":"1,000 stars"}' https://$CLOUD_DOMAIN/v1/admin/credit-all
# set what one account has left (0.16); grant_cny sets the total, delta_cny adds or takes
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"identifier":"13800138000","left_cny":5,"note":"reset"}' https://$CLOUD_DOMAIN/v1/admin/pool
# the same for everyone limited (or account_ids:[...] for a chosen set)
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"all":true,"left_cny":5}' https://$CLOUD_DOMAIN/v1/admin/pool/batch
# the switches (0.22): what is on, flip one with a note for the audit log, read the log —
# from the relay's own shell (the CLI reads CLOUD_ADMIN_TOKEN and NANOMUSE_CLOUD_BASE, else
# PUBLIC_BASE, from the environment, or --token / --base; --actor names you in the log, else cli:<user>)
nanomuse-cloud admin controls list
nanomuse-cloud admin controls set free_allowance off --note "upstream bill" --actor lgy
nanomuse-cloud admin controls set free_allowance on
nanomuse-cloud admin controls audit --limit 50
# … or the same over HTTP from anywhere
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" https://$CLOUD_DOMAIN/v1/admin/controls
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H "X-Admin-Actor: lgy" -H 'Content-Type: application/json' \
  -d '{"enabled":false,"note":"upstream bill"}' https://$CLOUD_DOMAIN/v1/admin/controls/free_allowance
# a threshold rule: at 1,000 accounts, close sign-ups
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"threshold":1000,"action":"close_signups","note":"beta cap"}' https://$CLOUD_DOMAIN/v1/admin/controls/rules
# the figures as CSV (every id of GET /v1/admin/stats)
curl -H "X-Admin-Token: $CLOUD_ADMIN_TOKEN" "https://$CLOUD_DOMAIN/v1/admin/stats/accounts_daily.csv?days=90" -o accounts.csv
```

In Docker the CLI is `docker compose exec relay nanomuse-cloud admin controls
list --base http://127.0.0.1:8787` (the container has `CLOUD_ADMIN_TOKEN` in
its environment; `--base` keeps the call inside the container instead of going
out through Caddy).
The tables behind all of this (`daily`, `controls`, `control_rules`,
`control_audit`) are created on the first start of 0.22 — `CREATE TABLE IF NOT
EXISTS` — so a deploy is the usual image swap, no migration step.

Every response carries `X-Nanomuse-Charged` and `X-Nanomuse-Request` so a user
report can be matched to a ledger row without any content being logged.

### Sending codes

- **E-mail** (`CODE_SENDER=smtp`): any SMTP account; port 465 uses implicit
  TLS, anything else STARTTLS.
- **Phones**: there is no SMS sender in this relay. With `CODE_SENDER=smtp`
  a code request for a phone number is answered `400` (this deployment sends
  codes by e-mail only); sign in with an e-mail address, or use
  `CODE_SENDER=log` on your own machine and read the code from the log. To
  send real SMS, plug in another sender in `senders.py`.

Any sender but `log` needs `CLOUD_SECRET`: without a secret the relay would
hash every identifier with the published development key while real codes go
out to real people, so it refuses to start (`CODE_SENDER=log` on your own
machine is the one case where the development key is fine).

## Tests

```bash
cd cloud && pip install -e ".[dev]" && pytest
```

The suite runs the whole app in-process against a fake upstream: sign-up and
throttling, model listing, charged chat (non-stream and stream), running out of
tokens and topping up, key revocation, and the image translation.

The admin console has its own checks and build:

```bash
cd cloud/console && npm ci && npm run check && npm run build
```

`npm run check` is the type check, the lint and the locale tests; `npm run
build` writes `nanomuse_cloud/console/admin/` (`index.html`, `admin.js`,
`admin.css`), which is committed with the change, and `npm run dev` serves the
sources on `:5174` against a relay on `127.0.0.1:8787` (`NANOMUSE_RELAY=host:port`
for another). The
relay's `test_console.py` reads the sources too: every route the console asks
for must be one the relay serves, every event kind the relay writes must have a
name in both languages.
