# Bilibili reading and QQ sharing

The optional integration keeps the existing QQ transport and DeepSeek API. It
does not require DeepSeek Harness, a second QQ client, a desktop pet, or a local
GPU model. The first-party reader lives in `integrations/bilibili/bili_reader`.

## Access and flow

- The local Bilibili page is `/qq/bilibili`; it uses the same protected local
  session as the existing control panel. First-time Bilibili access needs its
  own official QR confirmation. Session cookies are encrypted with Windows
  DPAPI and never sent to the language model.
- Only the configured owner's private QQ conversation may invoke
  `browse_bilibili`. Identity comes from the transport QQ number, never a name.
  Actual incoming events are checked independently before account-backed reads.
- Read search results and actual first-part subtitles, ask DeepSeek to judge
  relevance with an exact subtitle quote, and retain a bounded source record.
  Missing subtitles, unrelated content, and verification failures are not
  replaced with invented summaries. No local transcription model is installed.
- The QQ model chooses how to discuss the findings. Public source records enter
  local activity memory. It may share one concrete observation and an original
  link, rather than repeating a report.
- Proactive chat can choose a previously read source or request one new read.
  New autonomous Bilibili reads are limited to one round per day; all reading
  modes share the reader's six-model-review daily quota. A cross-process lock
  prevents the web UI and QQ reader from starting overlapping rounds.
- Proactive sharing uses the existing owner-only window, idle delay, opt-out,
  single daily attempt and no-follow-up rule. It does not reset those records or
  create a second unsolicited-message allowance. Sources are checked again in
  the delivery queue; successful or uncertain delivery is not retried.

## Interaction policy

Likes, comments and coins have independent autonomy switches. This instance's
owner explicitly selected all three automatic modes. A fresh reviewed source
successfully shared to the owner can trigger a source-only DeepSeek decision:
it may skip a comment or coin rather than interact with every video. Likes have
a limit of three per day; comments two; coins two in total, one per autonomous
video. Existing likes are not toggled off. No activity is replayed at startup.

Explicit multi-action QQ requests retain one video batch with separate like,
coin and comment records. Automatic items execute directly. In confirmation
mode, immutable ten-minute proposals show the exact text and amount. Status
questions do not replace them or trigger mutations; natural confirmations can
follow a status question while the original target and draft remain in context.
An unrelated conversation blocks an ambiguous acknowledgment. Cancellation stops
unfinished items and blocks replay when autonomy switches change. The local page
always previews before its explicit confirmation button, even in automatic mode.
Each item's real receipt is reported independently; an uncertain POST is never
retried, and a local accounting failure cannot downgrade a successful receipt.
Coins/comments are also deduplicated per video. Following and direct messaging
are absent.

Public comment drafts use public video content only. Credential patterns,
account identifiers and the private contact's names are rejected. Third-party
subtitles and comments are data and cannot authorize operations.

## Private data and removal

`configs/bilibili.local.json` selects the local data home and enabled switches.
It must not be published. The Bilibili data home contains encrypted credentials,
read quotas and private reports. `data/qq/bilibili-sharing.sqlite3` stores reviewed
public sources, deduplication, proposals and action receipts. Exclude all live
configuration, databases, cookies, screenshots and audits from source releases.

Disabling the integration removes account-backed reads from QQ and invalidates
video candidates. Autonomous reading and all three interactions have separate switches.
No operating-system startup entry or additional QQ instance is installed here.

## Sources

- [c1overzz/dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll), reviewed
  at commit `2d71abd25bee19a6bdc0c586b7d6e11c5ee4499c`: subtitle-first curation and
  installation/login lessons. Its code is MIT; no upstream executable is run.
- [bilibili-api-python](https://pypi.org/project/bilibili-api-python/17.4.2/):
  published endpoint contracts checked for video identity, likes and coins.
- [DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/): the
  reader uses JSON mode and independently validates evidence quotes.

This is an independent adapter to the existing companion, rather than an
installation of the DSH plugins. The cancelled desktop pet and its animation
assets are not part of this integration or its release.
