# Mail reference batch lookup

Local measurement on 2026-10-03: Windows build 26200, x64 CPython 3.11.15,
NTFS fixed drive, user TEMP directory. Baseline: `754c7873544e1c5338cfbe2451e2f930366fce2b`.
Each time is the median of three samples in seconds.

| Saved refs | 100-header batch | Before | After | Mapping reads before | After |
| --- | --- | ---: | ---: | ---: | ---: |
| 100 | Existing IDs | 1.0034 | 0.0603 | 10,000 | 100 |
| 100 | Mixed IDs | 1.1714 | 0.0938 | 10,925 | 100 |
| 2,000 | Existing IDs | 28.7062 | 4.4817 | 200,000 | 2,000 |
| 2,000 | Mixed IDs | 27.6537 | 4.0212 | 200,925 | 2,000 |

The substantial mailbox cases took 84.4% and 85.5% less time, respectively,
with at least 99% fewer mapping reads. Mixed batches contain 50 existing IDs
and 25 new IDs repeated twice. Existing batches contain 100 distinct saved IDs.
Returned headers and every saved mapping's name and bytes matched exactly
between baseline and optimized implementations for every sample.

The probe creates separate temporary mailboxes for each sample. It counts
`Path.read_text` calls on mapping files and times only `apply`, including its
lock and any durable writes. Seeding and parity checks are outside the timer.
Imports are warm; filesystem caches are not flushed. Other local tests ran
during measurement, so these are observed local timings, not isolated hardware
benchmarks, CI timings, or provider latency estimates.

Reproduce from the repository root in PowerShell:

```powershell
$baseline = Join-Path $env:TEMP 'mail-ref-baseline.py'
git show 754c7873544e1c5338cfbe2451e2f930366fce2b:agentself/internal/mail_state.py |
    Set-Content -Encoding utf8 $baseline
uv run --no-sync python benchmarks/mail_ref_probe.py $baseline
```

The lookup uses memory proportional to saved references for one locked
operation. It is rebuilt for the next operation; no database, persistent
cache, public interface, or saved-format migration is required. Regression
tests cover repeated IDs, gaps in preexisting refs, refreshed state across
calls, duplicate/corrupt mappings, input validation and partial progress,
unsafe directories and symlinks, exhaustion, and concurrent process writers.
