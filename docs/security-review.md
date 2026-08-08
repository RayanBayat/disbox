# Security review

Reviewed 2026-08-08 against the implementation at `0ede14a`.

> **This is a self-review by the author of the code, which is the weakest kind.**
> It finds what the author thought to look for, and is systematically blind to
> anything they misunderstood while writing it. It is not a substitute for an
> independent audit and must not be recorded as one.

---

## Findings

### 1. Path traversal via node names — **fixed**

**Severity: high.** A name entering the vault becomes a path on the way out: a
download writes `destination / node.name`. The name check rejected `/` and
control characters but **not backslash**, which is the path separator on
Windows — the primary target platform.

```
_ILLEGAL_NAME = re.compile(r"[/\x00-\x1f]")   # before
```

A node named `..\..\evil` was accepted, and downloading the folder containing it
would write outside the chosen destination. Reachable by anyone who can put a
name into a vault the victim later downloads from — a shared vault, or a vault
file received from someone else.

Fixed by rejecting `\ : * ? " < > |` in addition to `/` and control characters.

### 2. NTFS alternate data streams — **fixed**

**Severity: medium.** `a:b` was accepted. On NTFS, opening `dir/a:b` for writing
creates an *alternate data stream* attached to `a` rather than a file named
`a:b`. Content written that way is invisible to ordinary directory listings.
Closed by the same character rule.

### 3. Windows reserved device names — **fixed**

**Severity: low.** `CON`, `NUL`, `COM1`, `LPT9` and friends were accepted.
Opening one on Windows talks to a device rather than creating a file, so a
download containing one hangs or fails in a way that has nothing to do with
storage. Now rejected, including with an extension (`con.txt` is also reserved).

### 4. Trailing dots — **fixed**

**Severity: low.** Windows silently strips a trailing dot, so `report.` and
`report` are distinct in the vault and the same file on disk; the second
download overwrites the first. Now rejected. Trailing *spaces* were already
normalised away by the existing `strip()`, so they cannot produce the same
mismatch, and are left as normalisation rather than an error.

---

## Reviewed and found sound

**SQL injection.** Every query is parameterised. Four `f`-string statements
exist, all `PRAGMA`, which cannot take bound parameters; each interpolates a
module constant and never caller input. Verified by reading each call site.

**Secret redaction.** `disbox.log` runs redaction as a mandatory processor
rather than opt-in, in two independent layers — key-based and pattern-based —
and runs *after* exception formatting, so tracebacks are scrubbed too. That
ordering is the important part: HTTP clients routinely embed the full request
URL, credentials included, in error messages.

**Token handling.** The bot token is a `SecretStr`, so it is not rendered by
`repr` or in tracebacks. The settings dialog never displays it back, reporting
only whether one is configured, and an empty field means "unchanged" rather than
"erase". It reaches the wire only as an `Authorization` header.

**Chunk integrity.** AES-256-GCM authenticates every chunk, so tampered storage
fails its tag before decryption produces anything. See the note below about what
the manifest hash does and does not add.

**Vault opening.** A damaged vault is refused at open time via
`PRAGMA quick_check`, before migrations run, so migrating never spreads damage.

---

## Accepted risks

**Convergent encryption is confirmable.** A chunk's key derives from its own
plaintext hash, which is what makes deduplication possible. Someone holding the
master key can therefore confirm whether a **specific file they already have**
is present in the vault. It reveals nothing about content they do not already
possess. Documented in the README; reversible to per-file keys if the trade-off
is judged wrong.

**The manifest hash is not corruption detection.** `TransferEngine._unseal`
compares recovered bytes against the recorded digest, but the chunk key derives
from that digest, so anything that decrypts at all was sealed under it. Tampered
storage fails GCM authentication first. The comparison guards against *this
codebase* producing wrong bytes. Keep it, but do not credit it with catching
attackers.

**No rate limiting on passphrase attempts.** Argon2id makes each attempt
expensive, which is the intended defence for a local file. An attacker with the
vault file can attempt offline regardless, so an in-application limit would add
nothing.

---

## Not reviewed

- **The Discord backend against a live API.** Live tests exist but are excluded
  by default and were not run as part of this review.
- **Dependency vulnerabilities.** `pip-audit` runs in CI; its findings are not
  reproduced here.
- **The threat model in `ANALYSIS.md`** has not been re-validated line by line
  against the finished implementation.
- **Cryptographic primitives themselves.** Argon2id, AES-GCM and BLAKE3 are used
  through `cryptography` and `blake3`; their implementations were taken on trust.
- **Timing side channels** in passphrase verification were not measured.
