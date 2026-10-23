# Windows guided one-click deployment

This branch adds `deploy-whale.cmd`. It is a guided bootstrap, not an unattended QQ account installer. It does not require Clash or ChatGPT to start the core application.

## Prerequisites and first run

1. Install **64-bit Python 3.12**, including its `py` launcher, from the official Python Windows installer. This project never downloads or installs system Python, requests administrator privileges, changes PATH, firewall rules, or PowerShell execution policy.
2. Extract the repository into your own writable folder. Unicode and spaces are supported; do not extract an update over existing private data without a backup. Double-click `deploy-whale.cmd`.
3. Choose option 1. On first install, type `INSTALL` to approve downloading the base Python dependencies from PyPI into this project's `.venv`. Wheel-only installation fails rather than compiling arbitrary source packages. Optional GPU/model/mobile dependencies are not installed. Requirements currently use reviewed version ranges, not a fully hash-locked release; this is not a reproducible binary distribution.
4. Without configuration, a local explanation page opens on `127.0.0.1:18767`. Close the launcher, double-click it again, and choose option 2 to run the existing configuration wizard. Type `CREATE` only if you want to create persistent local access tokens and enter the dedicated bot/admin QQ IDs. API keys are not requested by this launcher. Existing or partial configuration is never overwritten, reset, or claimed to be repaired. A failure can leave partial files: inspect them locally before retrying, never upload them.
5. With all three core configuration files present, the actual `phone_server` runs on `127.0.0.1:18766/deployment` in **safe-start mode**. Presence alone is not configuration validity; invalid configuration can prevent startup. No QQ connection, login restoration, proactive task, life scheduler, or balance poll is initiated by startup. An internal idle mobile watchdog can exist; this is not a claim of zero background threads.
6. Click to authenticate locally and review the exact bot ID/allowlist. A separate checkbox and single-use, 120-second confirmation allow the QQ connection and existing device/saved-login restoration. An optional API Key password field saves only after a separate persistent-storage checkbox and explicit click. It never reveals an existing Key or contacts the provider to validate it. No new QQ password is collected here. Replies remain paused; enter QQ management to explicitly decide whether to enable them. Pending account-switch workflows are rejected so they cannot automatically re-enable replies.

This is not evidence of successful QQ login, messaging, platform compatibility or full QQ capabilities. NapCat installation, API account/billing, and test-account approval remain operator steps. After intentional connection, the normal management pages and their existing actions are available. Proactive/life/balance workers remain stopped for this safe-start process; this release does not add an automatic resume button for them.

## Repeated runs and failures

- `.venv` is project-local. Interpreter version/bitness/prefix, requirement versions, dependency consistency and recorded requirement hash are checked. An unchanged, healthy installation is reused without pip upgrades. Broken/unrecognized environments are preserved, not deleted or replaced.
- A project-local lock prevents simultaneous deployment operations. A crash can leave `.whale-deploy.lock`; first check Task Manager, then remove only that stale lock manually. The tool never kills processes or automatically removes an ambiguous lock.
- Occupied/unrecognized ports stop startup. A matching local health identity is used to reuse the existing console, not as security protection against a malicious process running as the same OS user. No credentials are sent to health probes. HTTP proxy variables and redirects are disabled for probes.
- The server remains in a separate console. Close that console or use Ctrl+C to stop it. Closing the menu does not terminate the application. There is no auto-run service or scheduled task.
- Download failure does not write a success record. Dependencies can remain partially installed, and the next explicitly approved install may retry. No global-install fallback exists.
- Existing data, media, memories and configuration are not deleted. The launcher runs the server from `.venv`, but paths in existing local configuration are not rewritten; optional workers can still reference old environments and must be checked manually.
- Setup confirmation uses native fetch, not the legacy auto-renew/retry wrapper. Once connection is submitted, double-click, HTTP 401 and transport failure never trigger a second submission. An uncertain result requires manual inspection, not automatic retry.

## Maintainer testing

`python -m unittest tests.test_deployment -q` exercises mock install/failure/reuse, Unicode/spaced paths, configuration preservation, process/port guards, same-origin authentication, replay/expiry, pending account switches, uncertain connection and blocked pre-consent routes. No real QQ accounts or credentials are used. The main Windows CI additionally runs the entire repository suite and public-source audit.

Real clean-machine installation, browser visual checks, downloaded-wheel installation on a non-admin Windows account, and QQ acceptance are separate checks and must not be inferred from unit-test success. For live testing, specify an independent test account and any intended post/message/recipient before taking the relevant action; never store screenshots containing tokens or login codes.
