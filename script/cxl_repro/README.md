# CXL write-allocate reproduction

Down-scaled reproduction of a measured problem on Emerald Rapids + CXL 1.1:
write-allocate ownership requests to CXL memory fetch a full line over the link's
read direction even when the line is fully overwritten. On a full-duplex link this
spends read-direction bandwidth that demand reads need, so mixed read/write
workloads lose throughput that non-temporal stores do not.

Everything added for this lives behind `[CXLREPRO]` comment tags and is inert at
default settings — building the branch's original config reproduces the original
binary's statistics bit-for-bit.

## What was added

| Component | Where | What it does |
|---|---|---|
| Duplex link mode | `inc/dram_controller.h`, `src/dram_controller.cc`, config `"duplex": true` | Per-direction bus slot and availability clock; no write-mode switching or turnaround. Direction comes from the request's `is_write` tag. Banks and bankgroup cooldown stay shared between directions, so total media throughput is below the sum of the two direction ceilings — as on a real device. Bank scheduling priority alternates per cycle; a fixed read-first order starves writebacks. |
| NT-store proxy | `src/cache.cc` (`handle_miss`) | With `CXL_STORE_POLICY=nt`, a first-level store miss to a far address skips allocation and streams the line to the far write queue. Falls back to the normal merge path when an MSHR for the line is already in flight. A full far WQ fails the tag check so the store retries, modeling write-combining backpressure. |
| Directional accounting | `inc/dram_stats.h`, `src/plain_printer.cc`, `src/cxl_repro.cc` | Per-channel `RD_LINES`/`WR_LINES` and per-direction bus-busy time; LLC-to-far `far_rfo_fetches` (the `unc_m_cas_count.rd` analogue), `far_demand_reads`, `far_prefetch_reads`, `far_writebacks`; L1D `nt_bypass_lines`/`nt_bypass_retry`; `pending_write_backlog()` for an exact write-conservation check. |

## Runtime knobs

No rebuild needed:

- `CXL_STORE_POLICY=allocate|nt` — write-allocate (default) or the NT-store proxy.
- `CXL_ALLOC_POLICY=only_far|first_touch|round_robin` — overrides the page placement policy.
- `CXL_LIVELOCK_IPC=<float>` — die threshold of the branch's livelock heuristic. Saturated
  far-memory workloads legitimately run below the stock 0.01 IPC and get killed by it. The
  true zero-progress deadlock detector is unaffected.

## Reproducing

Traces and the `tracegen` binary are generated, so they are not committed.

```sh
./config.sh cxl_repro_config.json && make -j

cd script/cxl_repro
gcc -O2 -o tracegen tracegen.c
mkdir -p traces
for R in 0 4 8 9 12 16; do ./tracegen $R 32 | gzip -1 > traces/mix_r$R.champsim.gz; done
./tracegen 0 8 -1     | gzip -1 > traces/clash.champsim.gz      # store hits an in-flight load MSHR
./tracegen 0 1  512   | gzip -1 > traces/small.champsim.gz      # footprint below the LLC
./tracegen 0 32 1024  | gzip -1 > traces/reuse_1k.champsim.gz
./tracegen 0 32 262144| gzip -1 > traces/reuse_256k.champsim.gz

python3 run_verify.py run      # 20-run matrix, writes out/
python3 run_verify.py report   # rf-sweep table
python3 run_verify.py verify   # 15 invariants, exit 0 if all pass
```

`tracegen <R_out_of_16> <buf_mib> [reuse_dist_lines]` emits R loads and (16-R) stores per
16-line period, so the read fraction is exact at every point in the trace. A reuse distance
re-reads each stored line that many lines later; `-1` selects clash mode, where every line is
loaded and then stored by the very next instruction.

## What the invariants check

`run_verify.py verify` asserts both that the model is self-consistent and that the measured
signature is reproduced. Two conservation laws (reads: `RD_LINES` equals the request ledger;
writes: issued equals completed plus queued backlog) caught a real scheduling bug during
development, so keep them first. The rest cover the fetch-per-written-line ratio, read-direction
saturation under mixed traffic, the allocate-versus-NT gap and its disappearance at the pure-read
and pure-write endpoints, address routing, and the corner cases above.
