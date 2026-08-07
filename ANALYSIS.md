# Disbox Web Client — Repository Analysis & Modernization Plan

**Analyzed:** 2026-08-07
**Repo:** `C:\Users\Rayan\Documents\Code\discord_storage`
**Upstream:** https://github.com/DisboxApp/web · **License:** AGPL-3.0
**State:** Not a git repository locally (no `.git`), no `node_modules` installed.

---

## Table of Contents

1. [What the repo is](#1-what-the-repo-is)
2. [Tech stack](#2-tech-stack)
3. [Architecture & data flow](#3-architecture--data-flow)
4. [File-by-file map](#4-file-by-file-map)
5. [Problems — correctness bugs](#5-problems--correctness-bugs)
6. [Problems — security & privacy](#6-problems--security--privacy)
7. [Problems — architecture & reliability](#7-problems--architecture--reliability)
8. [Problems — stack rot & tooling](#8-problems--stack-rot--tooling)
9. [Fundamental limitations (not fixable by better code)](#9-fundamental-limitations)
10. [Proposed modern architecture (Python-centric)](#10-proposed-modern-architecture)
11. [Component-by-component design](#11-component-by-component-design)
12. [Code sketches](#12-code-sketches)
13. [Problem → fix traceability matrix](#13-problem--fix-traceability-matrix)
14. [Migration plan](#14-migration-plan)
15. [Alternative stacks considered](#15-alternative-stacks-considered)
16. [Quick wins if you keep the current stack](#16-quick-wins-if-you-keep-the-current-stack)

---

## 1. What the repo is

**Disbox** is a browser-based "free unlimited cloud storage" service that abuses **Discord as a blob store**.

The mechanism:

1. The user creates a private Discord server and a **webhook** in it, then pastes the webhook URL into the app. That URL *is* the account — there is no signup, email, or password.
2. On upload, the browser slices the file into ~25 MB chunks (`FILE_CHUNK_SIZE = 25 * 1024 * 1023` ≈ 24.98 MiB, `src/disbox-file-manager.js:6`) and POSTs each chunk as a Discord message attachment through the webhook.
3. Discord returns a **message ID** per chunk. The ordered list of message IDs is the file's "content pointer."
4. That pointer plus the file metadata (name, path, size, timestamps, parent) is stored in a **separate central server** — `https://disbox-server.fly.dev` (`src/disbox-file-manager.js:4`) — keyed by `sha256(webhookUrl)` as the user ID.
5. On download, the client resolves message IDs → CDN attachment URLs via the webhook API, fetches each chunk, and streams them in order into a local file via the File System Access API.

The clever bit is the **trust split**: the central server holds message IDs but *not* the webhook token, so it cannot read your files; Discord holds the bytes but not the filesystem structure. Neither party alone sees the whole picture.

The repo contains **only the web client**. The metadata server and the Chrome extension are separate, closed-off deployments — which is itself a major finding (see [§7](#7-problems--architecture--reliability)).

### Feature surface (from README + code)

| Working | Partial / broken | Missing |
|---|---|---|
| Webhook login, upload, download, delete files, delete *empty* folders, create folders, rename, navigate, share links, search/sort/filter (client-side), file-type icons, dark/light theme | `moveFile` (API only, marked untested, no UI), file editing (untested), progress reporting (edge cases produce `NaN`) | Delete non-empty folders, upload/download folders, multi-select actions (checkboxes render but are inert), right-click menu, mobile support, resumable transfers, encryption, quota/usage display |

---

## 2. Tech stack

### Runtime & framework

| Layer | Technology | Version | Status in 2026 |
|---|---|---|---|
| UI framework | React | `^17.0.2` | **2 majors behind** (React 19 current). Uses legacy `ReactDOM.render`. |
| Build tooling | Create React App (`react-scripts`) | `5.0.0` | **Officially deprecated** by the React team (Feb 2025). No longer receives fixes. |
| Routing | `react-router-dom` | `^6.2.1` | Behind (v7 current); pinned to a gh-pages `basename` hack. |
| Language | JavaScript (ES2020+), JSX | — | **No TypeScript**, no JSDoc types, no runtime validation. |
| State | React `useState` / `useEffect` only | — | `recoil` is declared as a dependency but **never imported**; Recoil was archived by Meta in 2025. |

### UI libraries — three overlapping design systems

| Library | Version | Notes |
|---|---|---|
| `@mui/material` + `@emotion/*` | `^5.4.1` | MUI v5; v7 is current. |
| `@mui/x-data-grid` | `^5.5.1` | The file table. Community edition — hence the manual row-mutation helpers in `App.js:100-126`. |
| `react-bootstrap` + `bootstrap` | `^2.1.2` / `^5.1.3` | Navbar, buttons, forms. |
| `@fortawesome/*` | `^6.3.0` | File-type icons (`FileIcon.js`). |
| `react-icons` | `^4.3.1` | Landing-page icons. |
| `react-file-icon` | `^1.1.0` | **Declared but never imported** — dead dependency. |

Three button systems (MUI `Button`, Bootstrap `Button`, raw `<input>`) coexist; `App.js` imports both and aliases one to `BsButton`.

### Domain / utility libraries

| Library | Purpose | Notes |
|---|---|---|
| `js-sha256` | Hashes the webhook URL into the server-side user ID | `disbox-file-manager.js:200,212` |
| `native-file-system-adapter` | File System Access API ponyfill for streaming downloads to disk | Also loaded **a second time** from a CDN in `public/index.html` |
| `pako` | DEFLATE-compresses the share payload before base64 | `App.js:243`, `File.js:55` |
| `react-native-mime-types` | Filename → MIME type | A React Native package used in a web app |
| `url-join` | Builds share URLs | `App.js:246` |
| `fetch-jsonp` | — | **Declared but never imported** — dead dependency |
| `web-vitals` | CRA boilerplate metrics | Collected, never reported anywhere |

### Infrastructure

- **Hosting:** GitHub Pages (`gh-pages` devDependency, `homepage: "https://disboxapp.github.io/web/"`, `basename={'/web'}` hardcoded at `src/index.js:24`).
- **SPA routing workaround:** the `rafgraph/spa-github-pages` 404-redirect hack (`public/404.html` + inline script in `public/index.html`).
- **Metadata API:** `https://disbox-server.fly.dev` — hardcoded, source not in this repo.
- **CORS bypass #1 (preferred):** a Chrome extension with a hardcoded ID `jklpfhklkhbfgeencifbmkoiaokeieah` (`disbox-file-manager.js:28`, `ExtensionDialog.js:6,16`).
- **CORS bypass #2 (fallback):** the public third-party proxy `api.allorigins.win` (`disbox-file-manager.js:42`).
- **Storage backend:** Discord webhook API (`discordapp.com/api/webhooks/{id}/{token}`).

### Testing / quality tooling

- `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event` are installed — and there are **zero test files** in the repo.
- ESLint config is CRA's default (`react-app`, `react-app/jest`) — no custom rules, no Prettier, no `.editorconfig`.
- No CI workflows, no Dockerfile, no `.env.example`, no `CONTRIBUTING.md`.

---

## 3. Architecture & data flow

```
                    ┌──────────────────────────────────────────┐
                    │  Browser (React SPA on GitHub Pages)     │
                    │                                          │
   webhook URL ────►│  localStorage["webhookUrl"]  (plaintext) │
                    │            │                             │
                    │            ├─ sha256() ──► userId        │
                    │            │                             │
                    │  ┌─────────▼──────────┐                  │
                    │  │ DisboxFileManager  │  in-memory tree   │
                    │  └────┬──────────┬────┘                  │
                    └───────│──────────│───────────────────────┘
                            │          │
        metadata (JSON)     │          │   chunk bytes (multipart)
                            ▼          ▼
        ┌───────────────────────┐   ┌──────────────────────────┐
        │ disbox-server.fly.dev │   │  Discord Webhook API     │
        │  /files/get/{sha256}  │   │  POST  ?wait=true        │
        │  /files/create/{uid}  │   │  GET   /messages/{id}    │
        │  /files/update/{uid}/ │   │  DEL   /messages/{id}    │
        │  /files/delete/{uid}/ │   └───────────┬──────────────┘
        └───────────────────────┘               │
          knows: names, paths, sizes,           │ attachments[0].url
                 message IDs                    ▼
          cannot: read file bytes    ┌──────────────────────────┐
                                     │  cdn.discordapp.com      │
                                     │  (CORS-blocked)          │
                                     └───────────┬──────────────┘
                                                 │
                       ┌─────────────────────────┴──────────────┐
                       │                                        │
            Chrome extension (preferred)          allorigins.win (fallback)
            hardcoded ID, Chrome-only             3rd party sees ALL bytes
```

**Upload path:** `App.onUploadFileClick` → `getAvailableFileName` → `DisboxFileManager.uploadFile` → `createFile` (POST metadata, get row ID) → `DiscordFileStorage.upload` (sequential chunk loop) → `updateFile` (POST size + JSON array of message IDs).

**Download path:** `App.onDownloadFileClick` → `pickLocationAsWritable` (File System Access API) → `downloadFile` → `getAttachmentUrls` (one `GET /messages/{id}` per chunk, sequential) → `fetchUrl` per chunk (extension, else proxy) → `writeStream.write(blob)` in order → `close()`.

**Share path:** `getAttachmentUrls` → `JSON.stringify` → `pako.deflate` → `btoa` → URL-safe alphabet swap (`+→~`, `/→_`, `=→-`) → embedded in the **URL fragment** of a `/file` link. `File.js` reverses it and downloads without ever touching the metadata server.

---

## 4. File-by-file map

| File | LOC | Role | Notable |
|---|---|---|---|
| `src/disbox-file-manager.js` | 463 | **The entire domain layer.** `DiscordWebhookClient` (rate limits, HTTP), `DiscordFileStorage` (chunk up/down/delete), `DisboxFileManager` (virtual FS + metadata API). | The only file worth porting wholesale. Contains most of the bugs. |
| `src/App.js` | 384 | Main file-manager screen. Grid, progress snackbar, all handlers. | 384 lines mixing UI, orchestration, error handling, and share-link encoding. |
| `src/Home.js` | 102 | Marketing landing page. | Includes a ~2 KB regex UA-sniffing `isMobile()` one-liner. |
| `src/Setup.js` | 54 | Webhook onboarding wizard. | Reads the input via `document.getElementById` instead of controlled state. |
| `src/File.js` | 111 | Public share-link download page. | Two payload formats (query param **and** fragment); passes `size` as a string. |
| `src/SearchBar.js` | 117 | MUI Autocomplete over a flattened tree; supports `ext:png`. | Two logic bugs (see §5). |
| `src/columns.js` | 127 | DataGrid column defs + Share/Download/Delete buttons. | `Delete` calls `getChildren()` on every cell render. |
| `src/PathParts.js` | 45 | Breadcrumbs. | **Stores JSX elements in `useState`**; `await`s synchronous calls. |
| `src/FileIcon.js` | 47 | MIME → FontAwesome icon. | Fine. |
| `src/ExtensionDialog.js` | 43 | Nags Chrome users to install the extension. | Hardcoded extension ID. |
| `src/NavigationBar.js` | 34 | Bootstrap navbar. | Fine. |
| `src/ThemeSwitch.js` | 62 | Styled MUI dark-mode toggle. | Theme is **not persisted** across reloads. |
| `src/file-utils.js` | 50 | MIME lookup, save-picker, byte formatting, name deduplication. | Fine. |
| `src/index.js` | 41 | Router + `ReactDOM.render`. | Legacy React 17 root API. |
| `public/index.html` | — | CRA template + gh-pages redirect script. | **Loads an unpinned CDN `<script type="module">` with no SRI.** |
| `public/404.html` | — | gh-pages SPA redirect shim. | Standard hack. |

---

## 5. Problems — correctness bugs

Ordered roughly by severity.

### 5.1 — Share links break silently (highest-impact, environmental)

`App.js:236` tells the user: *"Sharing this file will create a **permanent link**."* It embeds raw `cdn.discordapp.com` attachment URLs into the fragment.

Since Discord introduced **signed, expiring CDN URLs** (`?ex=…&is=…&hm=…`), those URLs stop working after roughly 24 hours. Every share link generated by this app is a time bomb, and the UI explicitly promises the opposite. Worse, the failure is silent — `File.js` just throws into the console.

**Root cause:** the design leaks a storage-provider implementation detail (a CDN URL) into a user-facing permanent artifact. A share link must point at *your* service, which resolves fresh URLs on demand.

### 5.2 — Bootstrap failure path is dead code

```js
// disbox-file-manager.js:194-213
let fileTrees = {};                      // an object
...
if (fileTrees.length === 0) {            // undefined === 0  →  ALWAYS false
    throw new Error(`Failed to get files for user.`);
}
const [chosenUrl, fileTree] = Object.entries(fileTrees).sort(...)[0];  // [0] is undefined → TypeError
```

`{}.length` is `undefined`, so the guard never fires. A bad webhook URL (or a server outage) produces `TypeError: Cannot destructure property of undefined` instead of the intended message. Combined with `App.js:64-76`, which has **no try/catch around `init()`**, the result is an unhandled promise rejection and a permanently blank screen with no explanation.

### 5.3 — Multi-account tie-break sorts on `undefined`

```js
// disbox-file-manager.js:210
Object.entries(fileTrees).sort((f1, f2) => f2[1].length - f1[1].length)[0]
```

`f1[1]` is the file-tree **object**, not an array. `undefined - undefined` is `NaN`, so the comparator is meaningless and the winner depends on `Object.entries` ordering. For a user whose data straddles the `discord.com` / `discordapp.com` hostname migration, this can silently select the *emptier* tree — their files appear to have vanished.

### 5.4 — `renameFile` corrupts paths containing the filename

```js
// disbox-file-manager.js:304
const newPath = path.replace(file.name, newName);
```

`String.replace` with a string pattern replaces the **first** occurrence anywhere. Renaming `/report/report.txt` → `final.txt` yields `/final.txt/report.txt`. The subsequent `getFile(newPath)` existence check and the returned value are both wrong. Should be a path-segment splice, not a substring replace.

### 5.5 — Tree traversal crashes on files

```js
// disbox-file-manager.js:226-232
if (file.children[pathParts[i]]) { ... }
```

Files have no `children` property. Any path that descends *through* a file — e.g. a stale breadcrumb, a search query, or `getAvailableFileName`'s probe loop — throws `TypeError: Cannot read properties of undefined (reading '…')` instead of returning `null`.

### 5.6 — Share encoding blows up on large files

```js
// App.js:244
btoa(String.fromCharCode.apply(null, encodedAttachmentUrls))
```

`Function.prototype.apply` spreads the `Uint8Array` into the argument list. Browsers cap that at ~65 000–125 000 arguments. A file with enough chunks (a few GB → hundreds of ~100-byte URLs, compressed) hits `RangeError: Maximum call stack size exceeded`. The fix is a chunked loop or `TextDecoder`/`FileReader`, but see §5.1 — the whole approach should be replaced.

### 5.7 — SearchBar: shadowed variable disables the `files` filter

```js
// SearchBar.js:4    function SearchBar({ fileManager, files = true, ... })
// SearchBar.js:23   const files = fileManager.getChildren(file.path || file);   ← shadows the prop
// SearchBar.js:30   } else if (files && f.type === "file") {                    ← checks the local object
```

The `files={false}` prop can never take effect; the local `files` object is always truthy.

### 5.8 — SearchBar: `in` operator against a `Set`

```js
// SearchBar.js:74
if (parts.join(" ") in baseOptions) { newOptions.unshift(...); }
```

`baseOptions` is a `Set`. The `in` operator tests **property** names, not set membership, so this is always `false` and exact-match results are never promoted to the top. Should be `baseOptions.has(...)`.

### 5.9 — Progress math produces `NaN`

`onProgress(bytesDownloaded, fileSize)` is called with `fileSize = -1` when unknown (`disbox-file-manager.js:54`), and `File.js:67` passes `searchParams.get("size")` — a **string**. `Math.round((value / total) * 100)` then yields negative values or `NaN`, which is fed straight into `<LinearProgress value={…}>` and rendered as `"NaN%"`.

### 5.10 — Errors are alerted, then re-thrown

Every handler in `App.js` does `alert(...); throw e;` (lines 162, 190, 207, 227, 264). Re-throwing from an async event handler produces an unhandled rejection that nothing catches — the user gets a browser alert *and* the app is left in an inconsistent state (`currentAction` is never cleared, so the toolbar stays permanently disabled until reload).

### 5.11 — `deleteFile` leaves the UI stale on directories

`deleteFile` (`disbox-file-manager.js:344-371`) only removes the node from `parent.children` inside the `file.type === 'file'` branch. Deleting an empty **directory** removes it server-side but leaves it in the in-memory tree — it reappears on the next navigation.

### 5.12 — Unbounded recursion on rate limits

```js
// disbox-file-manager.js:94-100
if (status === 429) { ...; return await this.fetchFromApi(path, {method, body, type}); }
```

No attempt counter, no cap, no jitter. A webhook that is persistently 429-ing (or a `retry_after` the server keeps extending) recurses forever. There is also no `AbortController` anywhere, so nothing can be cancelled.

### 5.13 — Inert checkboxes

`<DataGrid checkboxSelection …>` (`App.js:367`) renders a selection column, but no handler consumes `selectionModel`. Users select ten files and discover there is no bulk action.

### 5.14 — Miscellaneous

- `PathParts.js:23,25,28` — `await` on synchronous methods, and JSX elements stored in `useState` (they capture stale props and defeat reconciliation).
- `PathParts.js:36` — `useEffect` deps are `[props.path]` only; breadcrumbs go stale when `fileManager` changes.
- `SearchBar.js:46` — deps are `[rows]`, but the effect reads `fileManager`, `directories`, and `advanced`.
- `columns.js:113-114` — `fileManager.getFile()` + `getChildren()` run on **every render of every Delete cell**; O(rows × depth) work per frame.
- `App.js:131` — `const parent = await fileManager.getParent(path)` — result assigned and never used.
- `App.js:4` — `Dialog`, `DialogActions`, `DialogContent`, `DialogContentText`, `DialogTitle`, `Button` imported and unused.
- Theme choice is not persisted; every reload resets to dark.
- `Setup.js:11` — reads the credential via `document.getElementById`, bypassing React entirely; no validation that the URL is even a Discord webhook.

---

## 6. Problems — security & privacy

### 6.1 — The webhook URL is a bearer credential stored in `localStorage`

`Setup.js:12` writes the full webhook URL — which grants **complete read/write/delete access to every file** — into `localStorage` in plaintext. `localStorage` is:

- readable by **any** JavaScript that executes on the origin (one XSS, one compromised npm dependency, one malicious browser extension with host permissions → total account compromise);
- persistent forever, with no expiry, no revocation, and no "log out of other devices";
- shared across every tab and readable by any script CRA bundles.

There is no session concept, no re-authentication, no rotation. The app's own Setup page warns the user not to store the URL anywhere — and then stores it.

### 6.2 — Unpinned third-party script with no SRI

```html
<!-- public/index.html -->
<script type="module" src="https://cdn.jsdelivr.net/npm/native-file-system-adapter/mod.js"></script>
```

No version pin, no `integrity` attribute, no `crossorigin`. jsDelivr serves **whatever the latest published version is**, executing in the same origin as the app that holds the user's master credential in `localStorage`. This is a live supply-chain vector on the single most security-sensitive page of the product. It is also redundant — the same package is already an npm dependency and bundled.

There is no Content-Security-Policy header or meta tag anywhere to constrain it.

### 6.3 — All file bytes flow through an anonymous third-party proxy

The fallback download path (`disbox-file-manager.js:41-43`) sends every attachment URL to `api.allorigins.win`, which fetches and returns the bytes. For every non-Chrome user — Firefox, Safari, Edge without the extension, mobile — **100% of downloaded file content transits an unaffiliated free service in plaintext**, along with the signed CDN URLs (which are themselves capability tokens). The README acknowledges this; the UI does not surface it at download time.

### 6.4 — No encryption at rest, anywhere

File bytes sit on Discord's CDN unencrypted. The README's marketing copy — *"All your files are stored on Discord's servers, and we have no access to them"* (`Home.js:90`) — is true of the *Disbox server* and materially misleading about **Discord**, which has full access to every byte, applies automated content scanning, and can terminate the account. There is no client-side encryption option at all.

### 6.5 — Credentials in URL paths

`sha256(webhookUrl)` is the sole authenticator for the metadata API, and it is passed **in the URL path**: `/files/update/{userId}/{fileId}`. URL paths land in server access logs, reverse-proxy logs, CDN logs, browser history, and `Referer` headers. A leaked log file is a full metadata compromise for every user in it. Credentials belong in an `Authorization` header, and the value should be a rotatable token — not a deterministic hash of a permanent secret.

### 6.6 — No authorization checks are possible

Because the user ID *is* the credential, the server cannot distinguish authentication from identification. There is no per-object ACL, no signing, no nonce, no replay protection. Anyone who learns a `sha256(webhookUrl)` value can read, rename, and **delete** that user's entire metadata tree — permanently destroying access to the underlying Discord attachments even without the webhook token.

### 6.7 — Share links are unrevocable capability tokens

A share URL embeds the raw CDN URLs. There is no revocation, no expiry control, no access log, no password option, and no download counter. Once shared, it is out of your hands until Discord's signature expires (§5.1) — which is simultaneously too soon for the promised use case and entirely outside the user's control.

### 6.8 — Message-ID enumeration

`getAttachmentUrls` calls `GET /messages/{id}` on the webhook. Discord snowflake IDs are timestamp-derived and not secret; the security boundary is the webhook token alone. Nothing binds a message ID to a file, so a corrupted or malicious metadata response can make the client fetch and assemble arbitrary attachments from the user's own channel.

### 6.9 — Known-vulnerable dependency tree

`react-scripts@5.0.0` pulls in a long-unmaintained transitive tree (`nth-check`, `postcss`, `webpack-dev-server`, `svgo`, `serialize-javascript`). A fresh `npm install` on this `package.json` reports a substantial number of moderate-to-high advisories, and because CRA is deprecated **there is no upstream fix path** — `npm audit fix` cannot resolve them without ejecting or replacing the toolchain.

---

## 7. Problems — architecture & reliability

### 7.1 — The metadata server is a single point of total data loss

`SERVER_URL` is one hardcoded fly.dev instance. If it goes down, is abandoned, or loses its database:

- every user's message-ID lists are gone;
- the attachments still exist on Discord but are **unaddressable** — no filenames, no order, no association;
- there is **no export**, no backup, no local cache, no `Download my metadata` button, no self-host option in this repo.

This is the single greatest risk in the design, and it is entirely unmitigated. The server's source isn't even in this repository.

### 7.2 — No transactional integrity — orphans and dangling pointers by design

Two non-atomic sequences with no compensation logic:

- **Upload** (`uploadFile`, `disbox-file-manager.js:410-426`): create metadata row → upload N chunks → update row with the ID list. A failure at chunk *k* leaves *k* orphaned Discord attachments consuming space forever, plus a zero-byte metadata row. There is no cleanup, no retry, no resume.
- **Delete** (`deleteFile`, `disbox-file-manager.js:344-371`): delete the metadata row **first**, then delete the Discord messages. If the process dies in between, the attachments are orphaned permanently — the only record of their IDs was just destroyed.

No idempotency keys, no write-ahead log, no reconciliation job, no garbage collector.

### 7.3 — Everything is sequential

Uploads (`disbox-file-manager.js:158-166`) and downloads (`disbox-file-manager.js:59-66`) are strict `for` loops with `await` inside. Resolving message IDs to URLs is *also* a sequential loop of individual HTTP requests (`disbox-file-manager.js:141-149`). A 5 GB file is 200 chunks = 200 serialized round-trips just to *start* downloading. Effective throughput is a small fraction of what parallel transfers would achieve, and the landing page advertises "extremely high upload and download speeds."

### 7.4 — No resume, no retry, no cancellation

Close the tab at 95% of a 10 GB upload and you start over. There is no `AbortController`, no upload-session concept, no checkpointing, no exponential backoff for transient network errors (only the 429 special case), and no way to cancel an in-flight operation — the close button on the progress snackbar (`App.js:321`) merely hides the UI while the transfer continues.

### 7.5 — Whole chunks are buffered in memory

`readFile` (`disbox-file-manager.js:12-23`) calls `blob.arrayBuffer()` — materializing 25 MB per chunk in RAM — then wraps it in a *new* `Blob`, briefly doubling it. Downloads do the same via `.blob()`. On memory-constrained devices, and with any parallelism added naively, this is where it falls over.

### 7.6 — No integrity verification

No per-chunk checksum, no whole-file hash, no length validation, no ordering check beyond array position. Silent corruption — a truncated proxy response, a partial write — is undetectable. The user finds out when the archive won't open.

### 7.7 — Client-side state is authoritative and unsynchronized

`DisboxFileManager` holds the whole tree in memory and mutates it optimistically after each API call. There is no ETag, no version vector, no polling, no websocket. Two tabs (or two devices) diverge immediately, and the last writer silently wins. `create` returns a bare ID as `text/plain` (`disbox-file-manager.js:404`) rather than the created resource, so the client reconstructs what it *thinks* the server stored.

### 7.8 — Browser-only, Chrome-first, desktop-only

- Full-speed downloads require a **Chrome-only extension with a hardcoded ID**. Everyone else silently falls back to the third-party proxy.
- Downloads depend on the File System Access API; the ponyfill's fallback for Firefox/Safari buffers the entire file in memory.
- `Home.js:22` shows a "not supported on mobile" dialog. There is no mobile app, no CLI, no sync client, no FUSE mount, no API for scripting.

### 7.9 — Discord rate limits are handled naively

The limiter (`disbox-file-manager.js:79-105`) keys off a caller-supplied `type` string, not Discord's actual `X-RateLimit-Bucket` header, and state lives per-`DiscordWebhookClient` instance. Webhooks are limited to roughly 5 requests per 5 seconds per webhook — a hard ceiling of ~25 MB/s upload *at best*, with no shared coordination across tabs or a global bucket.

### 7.10 — No observability

No logging framework, no error reporting (Sentry/OTel), no metrics, no health checks. `web-vitals` is collected into `reportWebVitals()` and discarded. When a user reports "my download failed," there is nothing to look at.

---

## 8. Problems — stack rot & tooling

| Issue | Detail |
|---|---|
| **CRA is dead** | `react-scripts@5.0.0`. Deprecated by React core (Feb 2025). No security patches, no React 19 support, slow (Webpack 4-era config), no path to fix its audit findings. |
| **React 17** | `ReactDOM.render` (`index.js:22`) is removed in React 19. No concurrent features, no Suspense for data, no Server Components, no `useTransition` for the grid. |
| **Zero tests** | Three `@testing-library` packages installed, `npm test` configured, and not a single `.test.js` file. Every bug in §5 would have been caught by a unit test on `DisboxFileManager`. |
| **No types** | `DisboxFileManager` returns loosely-shaped objects that flow through 8 components. `file.content` is a **JSON string inside a field** (`JSON.parse(file.content)`), untyped and unvalidated. |
| **No CI** | No GitHub Actions, no lint gate, no build check, no dependency scanning, no preview deploys. |
| **Dead dependencies** | `recoil` (archived upstream), `fetch-jsonp`, `react-file-icon` — installed, never imported. `@testing-library/*` — installed, no tests. |
| **Three UI kits** | MUI + Bootstrap + two icon libraries. Large bundle, inconsistent visual language, duplicated primitives. |
| **No config** | `SERVER_URL`, the extension ID, the chunk size, the proxy URL, and the router basename are all hardcoded literals. No `.env`, no build-time config, no way to self-host without editing source. |
| **No a11y** | No ARIA labels on icon buttons, no keyboard navigation beyond MUI defaults, no focus management in dialogs, `alert()` for all errors. |
| **`react-native-mime-types`** | A React Native package used for MIME lookup in a browser app; `mime` or a small map would do. |
| **Deployment coupling** | `basename={'/web'}` and `homepage` hardcode the GitHub Pages path into the source. The `404.html` redirect hack mangles URLs and interacts badly with the fragment-based share links. |

---

## 9. Fundamental limitations

These are **not** engineering defects — no rewrite fixes them. They should drive the architecture.

### 9.1 — It violates Discord's Terms of Service

Discord's ToS and Developer Policy prohibit using the platform as a general-purpose file host or CDN. Realistic consequences: webhook deletion, message purges, server deletion, and **account termination**. Users can lose everything with no recourse and no appeal. Any responsible version of this product must say so prominently, and must not architecturally bet the user's data on a single non-consenting provider.

### 9.2 — Discord controls the durability guarantees, and offers none

Discord can (and does) change limits unilaterally: the free attachment cap has moved between 8 MB / 25 MB / 10 MB, CDN URLs became signed and expiring, and there is nothing preventing retroactive deletion of old attachments. `FILE_CHUNK_SIZE` is a hardcoded constant betting on a number Discord can change tomorrow — and if it shrinks, **existing files are unaffected but all new uploads break**.

### 9.3 — The trust split is weaker than advertised

Splitting metadata (Disbox server) from content (Discord) means neither party sees everything — but Discord sees **all your file bytes in plaintext**, and the Disbox server sees your full directory structure, filenames, and sizes. "Secure" in the marketing copy overstates both.

### 9.4 — Webhooks are the wrong primitive

Webhooks were chosen for onboarding simplicity (README §Discord API), at the cost of: no listing, no search, no pagination, no bulk operations, a 5-req/5-s ceiling, and a single token that is simultaneously the username, the password, and the storage key — unrotatable without losing access to every existing file.

### 9.5 — Deletion is not deletion

Deleting a webhook, leaving the server, or losing the metadata row does not remove attachments from Discord's CDN. Signed URLs already handed out remain live until expiry. There is no way to offer a real "delete my data" guarantee — which matters if anyone ever points GDPR at this.

> **Bottom line:** the correct architectural response is not "make Discord work better" — it is **treat Discord as one interchangeable backend behind an abstraction**, so the product survives losing it.

---

## 10. Proposed modern architecture

### Design principles

1. **Storage backends are pluggable.** Discord becomes one implementation of a `StorageBackend` protocol alongside S3/R2, Backblaze B2, local disk, and others. Directly addresses §9.1–9.2.
2. **The server is the only thing that talks to Discord.** Kills CORS, kills the Chrome extension, kills the allorigins proxy, enables range requests and streaming. Addresses §6.3, §7.8.
3. **End-to-end encryption is the default, not a feature.** The server and Discord see ciphertext only. Addresses §6.4, §9.3.
4. **Content-addressed chunks with Merkle verification.** Enables dedup, integrity, resume, and parallelism for free. Addresses §7.2, §7.3, §7.6.
5. **Everything is resumable and idempotent.** Upload sessions, per-chunk state, a reconciliation worker. Addresses §7.2, §7.4.
6. **Users can export and self-host.** A single-command metadata export and a Docker Compose stack. Addresses §7.1.

### Target stack

```
┌────────────────────────────────────────────────────────────────────┐
│  CLIENTS                                                            │
│                                                                     │
│  Web: Next.js 15 + React 19 + TypeScript 5.x                       │
│       TanStack Query · TanStack Table · Tailwind 4 · shadcn/ui      │
│       WebCrypto (AES-256-GCM) in a Web Worker                       │
│       Streams API for true streaming up/download                    │
│                                                                     │
│  CLI / sync: Python 3.13 + Typer + Rich  →  pip install disbox      │
│  Mount:      pyfuse3 (Linux/macOS) · WinFsp (Windows)               │
└──────────────────────────┬─────────────────────────────────────────┘
                           │  HTTPS · OAuth2 · signed URLs
┌──────────────────────────▼─────────────────────────────────────────┐
│  API — Python 3.13 · FastAPI · Pydantic v2 · uvicorn+uvloop        │
│                                                                     │
│  ├─ Auth:      Discord OAuth2 (Authlib) → JWT access + refresh      │
│  ├─ Metadata:  SQLAlchemy 2.0 async + PostgreSQL 17 + Alembic       │
│  ├─ Transfers: httpx.AsyncClient, bounded concurrency, tenacity     │
│  ├─ Streaming: StreamingResponse + HTTP Range (seekable video)      │
│  ├─ Limits:    aiolimiter, Discord-bucket-aware, Redis-coordinated  │
│  └─ Jobs:      arq (Redis) — GC, reconciliation, URL refresh        │
└──────────────────────────┬─────────────────────────────────────────┘
                           │  StorageBackend protocol
      ┌────────────────────┼────────────────────┬───────────────┐
      ▼                    ▼                    ▼               ▼
  DiscordBackend    S3Backend (R2/B2)    LocalBackend     TelegramBackend
  (bot token,       (boto3/aioboto3)     (filesystem)     (optional)
   real API)
```

**Why Python for the backend here:** the workload is I/O-bound fan-out over HTTP with rate limits, retries, and streaming — exactly what `asyncio` + `httpx` + `tenacity` are good at. It also unlocks the ecosystem this product actually needs: `cryptography` for E2EE, `pyfuse3` for a real mounted drive, `zfec`/`reedsolo` for erasure coding, `arq` for background reconciliation, and Typer for a scriptable CLI. Node would work for the API alone but gives you none of the mount/CLI/erasure-coding story.

### Dependency manifest (`pyproject.toml`)

```toml
[project]
name = "disbox"
requires-python = ">=3.13"
dependencies = [
  "fastapi>=0.118",
  "uvicorn[standard]>=0.38",
  "pydantic>=2.11",
  "pydantic-settings>=2.7",
  "sqlalchemy[asyncio]>=2.0.36",
  "asyncpg>=0.30",
  "alembic>=1.14",
  "httpx[http2]>=0.28",
  "tenacity>=9.0",
  "aiolimiter>=1.2",
  "authlib>=1.4",
  "pyjwt[crypto]>=2.10",
  "argon2-cffi>=23.1",
  "cryptography>=44.0",
  "blake3>=1.0",
  "arq>=0.26",
  "redis>=5.2",
  "typer>=0.15",
  "rich>=13.9",
  "structlog>=24.4",
  "opentelemetry-instrumentation-fastapi>=0.50b0",
]

[dependency-groups]
dev = [
  "pytest>=8.3", "pytest-asyncio>=0.25", "respx>=0.22",
  "hypothesis>=6.122", "ruff>=0.9", "mypy>=1.14",
  "testcontainers[postgres]>=4.9", "coverage>=7.6",
]
```

Managed with **uv** (10–100× faster than pip, lockfile-native), linted and formatted with **ruff**, type-checked with **mypy --strict**.

---

## 11. Component-by-component design

### 11.1 Storage abstraction — the keystone change

```python
from typing import Protocol, AsyncIterator

class StorageBackend(Protocol):
    """One blob store. Discord is merely one implementation."""

    max_blob_size: int          # replaces the hardcoded FILE_CHUNK_SIZE
    supports_range: bool

    async def put(self, data: bytes, *, idempotency_key: str) -> BlobRef: ...
    async def get(self, ref: BlobRef, *, byte_range: tuple[int, int] | None = None) -> AsyncIterator[bytes]: ...
    async def delete(self, ref: BlobRef) -> None: ...
    async def exists(self, ref: BlobRef) -> bool: ...
```

`max_blob_size` becomes **per-backend and runtime-queried** instead of a hardcoded constant, so a Discord limit change is a config value rather than a broken build. A user can migrate providers with a background job while their file tree stays identical — the direct answer to §9.1 and §9.2.

### 11.2 Chunking, integrity, and dedup

- Chunk with **FastCDC** (content-defined boundaries), not fixed offsets — an insertion at the start of a file re-uploads one chunk instead of all of them.
- Hash each chunk with **BLAKE3** → the chunk's content address. Store `hash → BlobRef` in Postgres.
- A file is an ordered list of chunk hashes plus a **Merkle root**; verify on every read. Fixes §7.6.
- Identical chunks upload once, globally (or per-user if you prefer strict isolation). Fixes the total absence of dedup.

### 11.3 End-to-end encryption

- Master key: **Argon2id** over a user passphrase (or a stored, wrapped random key).
- Per-file key via **HKDF-SHA256**; per-chunk nonce derived from `(file_key, chunk_index)`.
- **AES-256-GCM** per chunk (`cryptography` on the CLI, WebCrypto in a Worker on the web).
- Filenames and paths encrypted too; the server indexes over blinded search tokens.
- Hash **after** encryption for the storage address, **before** encryption for dedup — with the usual caveat that cross-user dedup on plaintext hashes leaks existence; default to per-user dedup.

Result: Discord stores opaque ciphertext, the API server stores opaque ciphertext, and §6.4 / §9.3 stop being marketing problems.

### 11.4 Transfers — parallel, resumable, retried

```python
sem = asyncio.Semaphore(settings.max_concurrent_chunks)  # e.g. 8

@retry(
    retry=retry_if_exception_type((httpx.TransportError, RateLimited)),
    wait=wait_exponential_jitter(initial=0.5, max=30),
    stop=stop_after_attempt(6),
)
async def upload_chunk(...): ...
```

- Upload sessions live in Redis: `session:{id}` → `{file_id, total_chunks, completed: {idx: blob_ref}}`, TTL 7 days. Resume replays only the missing indices. Fixes §7.4.
- `idempotency_key = blake3(session_id || chunk_index)` makes retries safe. Fixes §7.2.
- Downloads fan out with the same semaphore and reassemble in order via a bounded buffer, so memory stays at `concurrency × chunk_size` instead of unbounded. Fixes §7.5.
- Every operation takes a cancellation token; `asyncio.TaskGroup` cancels siblings cleanly on failure.

### 11.5 Rate limiting done properly

Key the limiter on Discord's actual `X-RateLimit-Bucket` header, store the state in **Redis** so all workers and all of a user's devices share one budget, and use `aiolimiter.AsyncLimiter` per bucket with a global fallback. Respect `Retry-After` with jitter and a hard attempt cap. Fixes §5.12 and §7.9.

### 11.6 Server-side streaming proxy — deletes three problems at once

```python
@router.get("/files/{file_id}/content")
async def stream_file(file_id: UUID, range: str | None = Header(None)):
    return StreamingResponse(
        assemble_chunks(file_id, byte_range=parse_range(range)),
        status_code=206 if range else 200,
        headers={"Accept-Ranges": "bytes", "Content-Length": ...},
    )
```

Because the server fetches from Discord, there is **no CORS problem**, so:

- the Chrome extension is deleted entirely (§7.8),
- `api.allorigins.win` is deleted entirely (§6.3),
- HTTP Range support means video/audio **stream and seek in the browser** — a genuinely new capability,
- and it works identically in Firefox, Safari, and on mobile.

### 11.7 Share links that actually work

Replace URL-embedded CDN links with a real share resource:

```
POST /api/v1/files/{id}/shares
  → { token, url, expires_at, max_downloads, password_protected }

GET  /s/{token}          → HTML landing page
GET  /s/{token}/download → streams via the proxy, resolving fresh CDN URLs
DELETE /api/v1/shares/{token}   → instant revocation
```

The token is opaque and random; the server resolves live URLs at request time. This fixes §5.1 (expiry), §5.6 (`btoa` overflow — no payload in the URL), and §6.7 (revocation, expiry, download caps, optional password, access log) in one stroke.

If you want to preserve zero-knowledge sharing, put the **decryption key in the URL fragment** (`/s/{token}#{key}`) — fragments are never sent to the server, so the link works while the server still cannot read the file. That is the right use of the fragment trick the current code half-invented.

### 11.8 Auth

Discord **OAuth2** via Authlib → the user's real Discord identity → short-lived JWT access token (15 min) + rotating refresh token in an `HttpOnly; Secure; SameSite=Strict` cookie. Storage credentials (bot token or webhook) are encrypted at rest with a server-side KMS key and **never touch the browser**. Fixes §6.1, §6.5, §6.6 — and gives you revocation, multi-device sessions, and audit logs, none of which exist today.

### 11.9 Data model

```sql
users        (id, discord_id, created_at, encrypted_backend_creds, kdf_params)
backends     (id, user_id, kind, config_jsonb, max_blob_size, is_default)
nodes        (id, user_id, parent_id, name_enc, kind, size, created_at,
              updated_at, deleted_at, version)          -- soft delete + optimistic concurrency
chunks       (hash BYTEA PK, backend_id, blob_ref, size, refcount)
node_chunks  (node_id, idx, chunk_hash)                 -- ordered manifest
shares       (token, node_id, expires_at, max_downloads, downloads, password_hash)
upload_sessions (id, node_id, state_jsonb, expires_at)
audit_log    (id, user_id, action, target, ip, at)
```

- `version` on `nodes` gives **optimistic concurrency** — fixes the silent last-writer-wins of §7.7.
- `refcount` on `chunks` drives safe GC.
- Path is derived from `parent_id`, so `renameFile` is a single-column update — §5.4 becomes structurally impossible.
- `deleted_at` (soft delete) gives you a trash bin and makes §7.2's delete ordering recoverable.

### 11.10 Background workers (arq)

| Job | Purpose | Fixes |
|---|---|---|
| `gc_orphaned_chunks` | Delete blobs with `refcount = 0` older than N days | §7.2 |
| `reconcile_backend` | Verify every referenced blob still exists; flag missing | §7.2, §9.2 |
| `verify_integrity` | Sample chunks, re-hash, compare to the Merkle manifest | §7.6 |
| `purge_expired_shares` | Enforce share expiry | §6.7 |
| `migrate_backend` | Move a user's data between providers, chunk by chunk | §9.1 |
| `export_metadata` | Emit a signed, self-contained JSON manifest | §7.1 |

### 11.11 Optional resilience: erasure coding

For anything important, split each chunk with **Reed–Solomon** (`zfec`) into k-of-n shards spread across *multiple* backends — e.g. 6-of-10 across Discord + R2 + B2. Losing an entire provider (§9.1's account-termination scenario) costs you nothing but a rebuild job. This is the feature that turns "fun hack" into "actually trustworthy," and it is only possible once storage is abstracted.

### 11.12 CLI and mounted drive

```bash
uv tool install disbox
disbox login
disbox cp ./movie.mkv disbox:/media/          # parallel, resumable, encrypted
disbox sync ~/Documents disbox:/docs --watch
disbox mount ~/disbox                          # pyfuse3 / WinFsp
disbox export --out backup.json                # your metadata, offline
```

Typer + Rich for the interface; the mount reuses the exact same async core with a read-through LRU chunk cache. Directly answers §7.8 (browser-only, desktop-only, no scripting) and §7.1 (no export).

### 11.13 Frontend rewrite

- **Next.js 15 / React 19 + TypeScript**, or **Vite + React 19** if you don't want SSR. Either replaces dead CRA (§8).
- **TanStack Query** for server state — caching, background refetch, optimistic updates with rollback. Replaces the hand-rolled `updateRowById`/`deleteRowById`/`addRow` trio in `App.js:100-126`.
- **TanStack Table** replaces `@mui/x-data-grid`, with working multi-select and bulk actions (§5.13) and no commercial-tier gap.
- **Tailwind 4 + shadcn/ui** — one design system instead of three (§8).
- **Zod** schemas at the API boundary — untyped `file.content` JSON strings stop existing.
- **Web Workers + WebCrypto + Streams API** for encryption and true streaming (no more 25 MB `ArrayBuffer`s on the main thread, §7.5).
- Toast notifications and inline error states instead of `alert()` (§5.10), with proper focus management and ARIA labels (§8).
- **Virtualized rows**, so a 50 000-file folder scrolls; `columns.js`'s per-render `getChildren()` (§5.14) becomes a memoized selector.

### 11.14 Quality and ops

| Concern | Tooling |
|---|---|
| Tests | `pytest` + `pytest-asyncio`, `respx` to mock the Discord API, `hypothesis` for chunking/reassembly properties, `testcontainers` for real Postgres. **Every bug in §5 is a one-line unit test.** |
| Types | `mypy --strict` on Python, `tsc --strict` on the frontend |
| Lint/format | `ruff` (Python), `biome` or eslint+prettier (TS) |
| CI | GitHub Actions: lint → typecheck → test → build → `pip-audit` / `npm audit` → deploy |
| Migrations | Alembic, autogenerate + review |
| Observability | `structlog` JSON logs, OpenTelemetry traces, Sentry, `/healthz` + `/readyz` |
| Packaging | Multi-stage Dockerfile + `docker-compose.yml` (api, worker, postgres, redis) → **self-hostable**, killing §7.1 |
| Config | `pydantic-settings` — no hardcoded URLs, IDs, chunk sizes, or basenames (§8) |

---

## 12. Code sketches

### 12.1 Discord backend, done right

```python
class DiscordBackend:
    """Bot-token based. Parallel, retried, bucket-aware, no CORS problem."""

    def __init__(self, token: str, channel_id: int, redis: Redis) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            headers={"Authorization": f"Bot {token}"},
            timeout=httpx.Timeout(30.0, read=300.0),
            limits=httpx.Limits(max_connections=20),
            http2=True,
        )
        self._buckets = BucketLimiter(redis)   # keyed on X-RateLimit-Bucket
        self._channel_id = channel_id

    @property
    def max_blob_size(self) -> int:
        return self._negotiated_limit          # discovered, never hardcoded

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, RateLimited)),
        wait=wait_exponential_jitter(initial=0.5, max=30),
        stop=stop_after_attempt(6),            # bounded — cf. §5.12
        reraise=True,
    )
    async def put(self, data: bytes, *, idempotency_key: str) -> BlobRef:
        if existing := await self._seen(idempotency_key):
            return existing                    # idempotent retry — cf. §7.2
        async with self._buckets.acquire("POST:messages"):
            r = await self._client.post(
                f"/channels/{self._channel_id}/messages",
                files={"files[0]": (idempotency_key, data)},
            )
            self._buckets.observe(r.headers)
            r.raise_for_status()
        msg = r.json()
        ref = BlobRef(message_id=msg["id"], attachment_id=msg["attachments"][0]["id"])
        await self._remember(idempotency_key, ref)
        return ref

    async def get(self, ref, *, byte_range=None) -> AsyncIterator[bytes]:
        url = await self._fresh_url(ref)       # re-resolved every time — cf. §5.1
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else {}
        async with self._client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            async for block in r.aiter_bytes(chunk_size=64 * 1024):
                yield block                    # streamed, never fully buffered — cf. §7.5
```

### 12.2 Parallel, verified, resumable upload

```python
async def upload(self, session: UploadSession, source: AsyncIterator[bytes]) -> Node:
    sem = asyncio.Semaphore(self.settings.max_concurrent_chunks)
    results: dict[int, ChunkRecord] = dict(session.completed)   # resume — cf. §7.4

    async def one(idx: int, plaintext: bytes) -> None:
        async with sem:
            digest    = blake3(plaintext).digest()              # integrity — cf. §7.6
            if rec := await self.chunks.find(digest):
                results[idx] = rec                              # dedup
                return
            ciphertext = self.crypto.seal(plaintext, file_key=session.key, index=idx)
            ref = await self.backend.put(
                ciphertext, idempotency_key=f"{session.id}:{idx}"
            )
            results[idx] = await self.chunks.record(digest, ref, len(plaintext))
            await session.checkpoint(idx, results[idx])         # crash-safe

    async with asyncio.TaskGroup() as tg:                       # cancels siblings on error
        async for idx, chunk in fastcdc(source, avg=self.backend.max_blob_size):
            if idx in results:
                continue
            tg.create_task(one(idx, chunk))

    manifest = [results[i] for i in sorted(results)]
    return await self.nodes.commit(
        session.node_id, manifest, merkle_root=merkle(manifest)
    )
```

### 12.3 Rename that cannot corrupt a path

```python
async def rename(self, node_id: UUID, new_name: str, *, expected_version: int) -> Node:
    validate_name(new_name)                       # no separators, no traversal
    stmt = (
        update(Node)
        .where(Node.id == node_id, Node.version == expected_version)   # cf. §7.7
        .values(name_enc=encrypt(new_name), version=Node.version + 1)
        .returning(Node)
    )
    if (node := (await self.db.execute(stmt)).scalar_one_or_none()) is None:
        raise ConflictError("modified by another session")
    return node
```

Because the path is derived from `parent_id` rather than stored as a string, the §5.4 substring bug has no place to occur.

---

## 13. Problem → fix traceability matrix

| # | Problem | Fix | Where |
|---|---|---|---|
| 5.1 | Share links expire silently | Server-side share tokens, URLs resolved live | §11.7 |
| 5.2 | Dead bootstrap guard → blank screen | Pydantic-validated responses, typed errors, error boundary | §11.13 |
| 5.3 | `NaN` sort picks wrong tree | Single OAuth identity — the hostname hack disappears | §11.8 |
| 5.4 | `renameFile` corrupts paths | `parent_id`-derived paths; rename is a column update | §11.9, §12.3 |
| 5.5 | Traversal crash on files | Typed tree, DB-side lookup, no client-side descent | §11.9 |
| 5.6 | `btoa` RangeError | No payload in the URL at all | §11.7 |
| 5.7/5.8 | SearchBar logic bugs | Server-side search over blinded index; unit tested | §11.3, §11.14 |
| 5.9 | `NaN%` progress | Typed `Content-Length`, Pydantic-parsed sizes | §11.6 |
| 5.10 | `alert()` + rethrow | Toasts + error boundaries + TanStack Query error state | §11.13 |
| 5.11 | Stale directory after delete | Query invalidation; server is authoritative | §11.13 |
| 5.12 | Unbounded 429 recursion | `tenacity` with `stop_after_attempt` + jitter | §11.5, §12.1 |
| 5.13 | Inert checkboxes | TanStack Table selection + bulk endpoints | §11.13 |
| 6.1 | Credential in `localStorage` | OAuth2 + HttpOnly cookies; backend creds never client-side | §11.8 |
| 6.2 | Unpinned CDN script, no CSP | Bundled deps, strict CSP, SRI, no external scripts | §11.13, §11.14 |
| 6.3 | Third-party proxy sees all bytes | Server-side streaming proxy | §11.6 |
| 6.4 | No encryption at rest | AES-256-GCM E2EE by default | §11.3 |
| 6.5 | Credentials in URL paths | `Authorization: Bearer` + rotating tokens | §11.8 |
| 6.6 | No authorization model | Per-user rows + policy checks + audit log | §11.8, §11.9 |
| 6.7 | Unrevocable share links | Share resource: expiry, revoke, cap, password, log | §11.7 |
| 6.8 | Message-ID substitution | Merkle verification binds content to the manifest | §11.2 |
| 6.9 | Vulnerable dep tree | uv + ruff + `pip-audit`/`npm audit` in CI; CRA gone | §11.14 |
| 7.1 | Metadata SPOF | Self-host via Docker Compose + one-command export | §11.10, §11.14 |
| 7.2 | Orphans, dangling pointers | Idempotency keys, refcounts, soft delete, GC worker | §11.4, §11.10 |
| 7.3 | Everything sequential | Bounded-concurrency `TaskGroup` fan-out | §11.4, §12.2 |
| 7.4 | No resume/retry/cancel | Redis upload sessions, `tenacity`, cancellation tokens | §11.4 |
| 7.5 | 25 MB buffers in RAM | Async streaming both directions | §11.6, §12.1 |
| 7.6 | No integrity checks | BLAKE3 per chunk + Merkle root + verify worker | §11.2, §11.10 |
| 7.7 | Unsynchronized client state | `version` column, optimistic concurrency, 409s | §11.9, §12.3 |
| 7.8 | Chrome-only, desktop-only | Server proxy (no extension), CLI, FUSE mount, mobile-ready | §11.6, §11.12 |
| 7.9 | Naive rate limiting | Redis-coordinated, bucket-header-keyed limiter | §11.5 |
| 7.10 | No observability | structlog + OpenTelemetry + Sentry + health checks | §11.14 |
| 8.* | Stack rot, no tests/types/CI | Python 3.13 + FastAPI + Next 15 + React 19 + TS + full CI | §10, §11.14 |
| 9.1/9.2 | Discord ToS & unilateral changes | **Pluggable backends** + runtime-negotiated limits + migration job | §11.1 |
| 9.3 | Trust split overstated | E2EE makes the claim literally true | §11.3 |
| 9.4 | Webhooks are limiting | Bot token via OAuth, or any non-Discord backend | §11.1, §11.8 |
| 9.5 | Deletion isn't deletion | E2EE + key destruction = cryptographic erasure | §11.3 |

---

## 14. Migration plan

Incremental, each phase independently shippable. Nothing requires a big-bang rewrite.

**Phase 0 — Stop the bleeding (1–2 days, current stack)**
Remove the unpinned CDN `<script>` from `public/index.html`; add a CSP meta tag; fix §5.2, §5.3, §5.4, §5.5, §5.8, §5.12; wrap `init()` in try/catch; stop re-throwing after `alert()`; add a **"Export my file tree"** button (metadata JSON) — the single highest-value mitigation for §7.1. Add a plain-language ToS warning (§9.1). No architecture change, big risk reduction.

**Phase 1 — Python API alongside the existing client (2–3 weeks)**
Stand up FastAPI + Postgres + Alembic. Port `DisboxFileManager` to `services/filesystem.py` **with tests first** — every §5 bug becomes a red test that turns green. Implement `StorageBackend` with `DiscordBackend` and `LocalBackend`. Add an importer that ingests an exported v1 tree. Run in parallel; the old client keeps working.

**Phase 2 — Server-side streaming proxy (1 week)**
Ship `GET /files/{id}/content` with Range support. Point the existing React 17 client at it. **Immediately deletes the Chrome extension dependency and the allorigins proxy** — the biggest single UX and privacy win available, with no frontend rewrite.

**Phase 3 — Auth (1 week)**
Discord OAuth2, JWT sessions, encrypted backend credentials. Migration path: users link their Discord account once, and their existing `sha256(webhookUrl)` tree is claimed and re-keyed.

**Phase 4 — Frontend rewrite (2–3 weeks)**
Next.js 15 + React 19 + TS + TanStack + Tailwind/shadcn. Ship route by route: `/setup` → `/s/{token}` → the file manager. The old client stays live until parity.

**Phase 5 — Transfer engine (2 weeks)**
FastCDC + BLAKE3 + dedup, parallel resumable uploads, arq workers for GC/reconciliation/verification.

**Phase 6 — E2EE (2 weeks)**
WebCrypto in a Worker + `cryptography` in the CLI. Opt-in first, then default for new files, with a background re-encryption job for old ones.

**Phase 7 — Beyond the browser (3 weeks)**
Typer CLI, `disbox sync`, `disbox mount` (pyfuse3/WinFsp), extra backends (R2/B2), optional erasure coding across providers.

---

## 15. Alternative stacks considered

| Option | Verdict |
|---|---|
| **FastAPI + Next.js** (recommended) | Best fit. Python's async I/O story suits rate-limited HTTP fan-out; unlocks CLI, FUSE, erasure coding, `cryptography`. React keeps the existing UI knowledge transferable. |
| **Litestar** instead of FastAPI | Genuinely faster, better DI, first-class SQLAlchemy plugins. Smaller ecosystem. Reasonable if you value performance over familiarity. |
| **Django + DRF/Ninja** | Free admin, auth, and migrations — attractive if you want a user dashboard cheaply. Async support is still less natural for this workload. |
| **Full-Python UI** (Reflex / NiceGUI / FastHTML+HTMX) | One language end to end, dramatically less code. But you lose Web Workers + WebCrypto + Streams — which E2EE and streaming uploads **require**. Viable only if you drop client-side encryption. |
| **Go / Rust backend** | Best raw throughput and a single static binary; Rust + `opendal` would be excellent for the storage layer. Slower to build, and you give up the Python ecosystem above. A Rust core with PyO3 bindings is a good *later* optimization for the chunker. |
| **Node/TypeScript everywhere** (Hono/NestJS) | One language, shared types via tRPC. But no good FUSE story, weaker crypto/erasure-coding libraries, and no meaningful advantage over Python for I/O-bound work. |
| **Keep it serverless** (Cloudflare Workers + D1 + R2) | Cheap, global, and Workers can proxy Discord. But 128 MB memory and CPU limits fight large-file streaming, and there is no place for the FUSE/CLI story. |

---

## 16. Quick wins if you keep the current stack

If a rewrite isn't on the table, these are ordered by value-per-hour:

1. **Delete the CDN `<script>`** from `public/index.html` — the npm package is already bundled. (§6.2, 5 minutes)
2. **Add a metadata export button** — `JSON.stringify(fileManager.fileTree)` → download. Removes the catastrophic-loss scenario. (§7.1, 30 minutes)
3. **Fix §5.2 and §5.3** — `Object.keys(fileTrees).length === 0`, and sort by `Object.keys(tree.children ?? {}).length`. (10 minutes)
4. **Fix `renameFile`** — splice the last path segment instead of `String.replace`. (§5.4, 10 minutes)
5. **Guard `getFile`** — `if (file.children?.[part])`. (§5.5, 5 minutes)
6. **Bound the 429 retry** — pass a depth counter, cap at 5, add jitter. (§5.12, 15 minutes)
7. **Stop re-throwing after `alert()`**, and clear `currentAction` in a `finally`. (§5.10, 20 minutes)
8. **Warn honestly on share** — replace "permanent link" with "this link stops working after ~24 hours." (§5.1, 5 minutes)
9. **Parallelize `getAttachmentUrls`** — `await Promise.all(ids.map(...))` with a small concurrency cap. Large, immediate download-latency win. (§7.3, 30 minutes)
10. **Add a ToS warning** on the Setup page. (§9.1, 10 minutes)
11. **Persist the theme** to `localStorage`. (§5.14, 10 minutes)
12. **Drop `recoil`, `fetch-jsonp`, `react-file-icon`** from `package.json`. (§8, 5 minutes)
13. **Write tests for `disbox-file-manager.js`** — it is pure logic over `fetch`, trivially mockable with MSW. This is what prevents the next §5. (a day)
14. **Migrate CRA → Vite** — mostly mechanical, fixes most audit findings and cuts dev-server startup from ~30 s to under a second. (§8, half a day)

---

## Summary

Disbox is a genuinely clever hack: a virtual filesystem over Discord attachments, with a trust split that keeps the metadata server blind to file contents. The core idea in `disbox-file-manager.js` is sound and worth preserving.

What holds it back falls into three tiers:

- **Bugs** (§5) — a dozen concrete defects, several of which produce silent data-integrity or availability failures. All are cheap to fix and all would have been caught by tests that were never written despite the test tooling being installed.
- **Architecture** (§6–§8) — a bearer credential in `localStorage`, an unpinned CDN script on the credential-holding page, all file bytes optionally routed through an anonymous third-party proxy, no encryption, no transactions, no parallelism, no resume, no integrity checks, and a metadata server whose loss would destroy every user's data with no export path.
- **Foundations** (§9) — the product is built on a provider that forbids this use, changes limits unilaterally, and has already broken the share feature by making CDN URLs expire.

The modernization is not primarily "rewrite it in Python." It is **three structural moves**, and Python happens to be the best language for all three:

1. **Abstract the storage backend** so Discord is interchangeable — this is the only real answer to §9.
2. **Put a server between the browser and Discord** — which simultaneously deletes the Chrome extension, the third-party proxy, and the CORS problem, and adds streaming and range requests.
3. **Encrypt client-side and content-address everything** — which makes the security claims true and gives you dedup, integrity, resume, and parallelism as by-products.

FastAPI + Postgres + Next.js 15/React 19 delivers all three, and Python additionally unlocks the CLI, the FUSE mount, and cross-provider erasure coding — the features that would turn this from a clever demo into something you'd trust with real data.
