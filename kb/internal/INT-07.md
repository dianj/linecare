---
doc_id: INT-07
title: "Line test interpretation"
audience: internal
group: technical
lang: en
version: 2
updated: 2026-07-30
---
# Line test interpretation (internal)

## Verdicts
- **no_sync**: the line has no synchronisation. With an active outage in the area, the outage explains it. Without
  one, it is a line fault: case and technician visit.
- **degraded**: signal-to-noise ratio below 6 dB, or 200 or more errors in 15 minutes. A line fault: case and
  technician visit.
- **ok**: the line is fine at the moment of the test.

## Intermittent drops
A line can test "ok" and still drop. Count the drops in the last 24 hours: 3 or more justify a technician visit;
fewer get a case with a monitoring note. Repeated cases for drops go to the network team as an internal ticket.

## Typical values
Attenuation up to about 30 dB is normal for DSL; above 40 dB the line is long or damaged. A healthy line shows an
SNR above 12 dB and fewer than 20 errors in 15 minutes.
