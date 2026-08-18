# TMCRA Memory for Windows

This directory is an independent Electron application. It gives a normal
Windows user one installation and account flow for the TMCRA Codex integration:

1. detect a compatible Codex installation;
2. verify and unpack the bundled TMCRA plugin into a stable per-user folder;
3. run the plugin installer without exposing a server credential;
4. complete device authorization in an isolated application window;
5. tell the user to restart Codex and review the three lifecycle Hooks.

The last step is intentionally not automated. Codex keeps Hook trust under the
user's control, and the app only records that the user has read the reminder.

## Build inputs

The build synchronizes the generated plugin release assets from the website's
`public/downloads/` directory into `resources/` before verification:

- `tmcra-codex-latest.zip`
- `tmcra-codex-release.json`

The release manifest SHA-256 is verified before the app unpacks the archive.
`npm run verify:resources` performs the synchronization and also checks that the
manifest version matches the plugin manifest inside the archive, so a clean
checkout can reproduce the installer without an undocumented copy step.

## Commands

```powershell
npm install
npm test
npm run dist:win
npm run publish:win
```

The NSIS build is per-user, uses an installation wizard, allows the destination
folder to be changed, and creates Start menu and desktop shortcuts. The current
build is intentionally unsigned; Windows SmartScreen may warn until TMCRA adds
an Authenticode code-signing certificate to the release process.

`publish:win` copies the completed installer into the ignored
`.release-assets/` directory and regenerates the public SHA-256 file and desktop
release manifest. The production gateway serves the large binary directly;
putting it under `public/` would exceed the Workers static-asset size limit.

## Security boundary

- Only the local renderer receives a preload bridge, with six argument-free
  IPC operations.
- Login, device authorization and `/personal` run in isolated BrowserWindows
  with Node disabled, context isolation and sandboxing enabled, and no preload.
- The application accepts the verification URL only when it is HTTPS and has
  the exact configured TMCRA authorization origin.
- Installer NDJSON is reduced to an allowlist. Tokens, device codes, PKCE
  verifiers and delivery receipts are never forwarded to the renderer.
- Closing or cancelling the setup terminates the PowerShell process tree owned
  by this application.
- The connected badge is not set by the browser approval alone. It is set only
  after the installer runs its non-metered authenticated service check and exits
  successfully. Quota and remaining quota are reported by the remote service
  and shown through the isolated `/personal` console.
- On a later launch, the app verifies that Codex still has the matching plugin
  registered from the stable integration directory and reruns the same remote
  service check before restoring the connected badge. It never infers connection
  from the presence of a local Token file.
