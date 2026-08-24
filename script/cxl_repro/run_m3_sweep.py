#!/usr/bin/env python3
"""M3: how small can the partial-line table be before it costs performance?

The paper's defensible position on proposal B is not that it is new -- it is
write-validate -- but that its cost is derivable and small.  The in-line form costs
17 bit/line (3.3% of the data array).  The cheaper form is a per-line PARTIAL bit plus
a small side table, and that only works if few lines are partial at once.  Nothing in
the architecture bounds that number: MSHRs are bounded by MLP and the store queue by the
ROB, but partial lines are bounded by the program.  This sweep measures it.

Overflow is not a correctness event.  A displaced line has lost its mask, so it is
fetched whole and behaves like an ordinary write-allocate line again -- graceful
degradation into the baseline.  That fetch is a real far request here (it must spend the
transaction budget it competes for), so a smaller table shows up as lost throughput.

Only `elide_safe` is swept: `elide` is the free-correctness upper bound and by
construction ignores the table, `allocate` has no episodes, and `elide_fetch` already
pays two transactions per partial line so the table cannot cost it much more.
Baselines come from M2 -- this sweep does not rerun them.

Note the 131072 point is NOT expected to reproduce M2's elide_safe exactly: M2's binary
counted displacements but exempted them from cost, so its figure was optimistic by
whatever the ~1% displacement rate was worth.  That delta is a result, not a discrepancy.
"""

import argparse
import concurrent.futures as cf
import json
import math
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.abspath(os.path.join(HERE, '..', '..', 'bin', 'champsim_cxl_repro'))
TRACES = '/mnt/local_storage/trace/champsim/v2_final'
M2 = '/mnt/local_storage/m2_campaign'
OUT = '/mnt/local_storage/m3_sweep'

WARMUP = 10_000_000
SIM = 50_000_000
POLICY = 'elide_safe'
SIZES = [16, 64, 256, 1024, 4096, 16384, 131072]

# Ten slices spanning the two axes that matter: how much of a line the stores cover
# (0% means every elided line ends partial) and how many lines are partial at once
# (live_max, which is what a finite table actually has to hold).  bwaves-34B is the
# control: 814 concurrent partial lines, so it should be flat across the whole sweep.
SELECTED = [
    '519.lbm_r-413B',        # cov   0.00%  live 24189  gain 1.842  <- flagship
    '505.mcf_r-542B',        # cov   0.00%  live  7990  gain 1.393
    '520.omnetpp_r-104B',    # cov   5.09%  live  2698  gain 1.075
    '505.mcf_r-197B',        # cov  18.99%  live  4726  gain 1.072
    '508.namd_r-750B',       # cov  60.96%  live 32082  gain 1.737
    '521.wrf_r-1957B',       # cov  71.52%  live 27363  gain 1.266
    '523.xalancbmk_r-520B',  # cov  94.55%  live 28875  gain 2.718
    '554.roms_r-172B',       # cov  95.33%  live 21287  gain 1.383
    '531.deepsjeng_r-614B',  # cov 100.00%  live 32228  gain 5.777  <- largest gain
    '503.bwaves_r-34B',      # cov 100.00%  live   814  gain 1.020  <- control
]
SUFFIX = '.champsimtrace.xz'


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def out_path(trace, size):
    return os.path.join(OUT, f'{trace}__{POLICY}__bind__n{size}.txt')


def done(path):
    return os.path.exists(path) and 'ChampSim completed all CPUs' in open(path).read()


def run_one(trace, size, period):
    path = out_path(trace, size)
    if done(path):
        return f'{trace} n={size}', 'cached'
    env = dict(os.environ, CXL_STORE_POLICY=POLICY, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001', CXL_MASK_ENTRIES=str(size),
               CXL_TX_PERIOD_PS=str(period))
    cmd = [BIN, '--size-trace', '--warmup-instructions', str(WARMUP),
           '--simulation-instructions', str(SIM), os.path.join(TRACES, trace + SUFFIX)]
    with open(path, 'w') as f:
        p = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    return f'{trace} n={size}', f'rc={p.returncode}'


def parse(path):
    if not os.path.exists(path):
        return None
    t = open(path).read()
    d = {'completed': 'ChampSim completed all CPUs' in t}
    m = re.findall(r'CPU 0 cumulative IPC: ([\d.]+)', t)
    if m:
        d['ipc'] = float(m[-1])
    cm = re.search(r'FAR_CHANNEL_0 RD_LINES:\s+(\d+) WR_LINES:\s+(\d+)', t)
    if cm:
        d['rd_lines'], d['wr_lines'] = int(cm.group(1)), int(cm.group(2))
    for k in ('episodes_opened', 'episodes_closed', 'evicted_by_conflict', 'live_now',
              'live_max', 'overflow_material', 'material_dropped', 'table_entries',
              'partial_writebacks'):
        mm = re.search(rf'CXLMASK {k}: *(\d+)', t)
        if mm:
            d[k] = int(mm.group(1))
    if all(k in d for k in ('episodes_opened', 'episodes_closed', 'evicted_by_conflict', 'live_now')):
        d['ledger_residual'] = (d['episodes_opened'] - d['episodes_closed']
                                - d['evicted_by_conflict'] - d['live_now'])
    return d


def m2_ref(trace, policy):
    p = os.path.join(M2, f'{trace}{SUFFIX}__{policy}__bind.txt')
    if not os.path.exists(p):
        return None
    m = re.findall(r'CPU 0 cumulative IPC: ([\d.]+)', open(p).read())
    return float(m[-1]) if m else None


def report():
    budgets = json.load(open(os.path.join(M2, '_budgets.json')))
    rows = []
    log(f'{"trace":22s} {"n":>7s} {"IPC":>8s} {"vs alloc":>9s} {"overflow":>10s} '
        f'{"conflict":>10s} {"far RD":>9s} {"far WR":>9s} {"led":>4s}')
    for tr in SELECTED:
        alloc = m2_ref(tr, 'allocate')
        safe2 = m2_ref(tr, 'elide_safe')
        for n in SIZES:
            d = parse(out_path(tr, n))
            if not d or not d.get('completed'):
                log(f'{tr[:22]:22s} {n:7d}   (incomplete)')
                continue
            rel = d['ipc'] / alloc if alloc else float('nan')
            d.update(trace=tr, size=n, rel=rel, m2_safe=safe2, m2_alloc=alloc)
            rows.append(d)
            log(f'{tr[:22]:22s} {n:7d} {d["ipc"]:8.4f} {rel:9.3f} '
                f'{d.get("overflow_material",0):10d} {d.get("evicted_by_conflict",0):10d} '
                f'{d.get("rd_lines",0):9d} {d.get("wr_lines",0):9d} '
                f'{d.get("ledger_residual",0):4d}')
        log('')
    with open(os.path.join(OUT, '_m3.json'), 'w') as f:
        json.dump(rows, f, indent=1)

    # Knee: the smallest table within 2% of the largest-table result for that trace.
    log('knee (smallest table within 2% of the n=131072 point):')
    knees = []
    for tr in SELECTED:
        pts = {r['size']: r for r in rows if r['trace'] == tr}
        top = pts.get(SIZES[-1])
        if not top:
            continue
        knee = next((n for n in SIZES if n in pts and pts[n]['ipc'] >= 0.98 * top['ipc']), None)
        knees.append(knee)
        loss16 = (1 - pts[SIZES[0]]['ipc'] / top['ipc']) * 100 if SIZES[0] in pts else float('nan')
        m2s = top.get('m2_safe')
        exempt = (1 - top['ipc'] / m2s) * 100 if m2s else float('nan')
        log(f'  {tr[:22]:22s} knee={knee}  loss@n={SIZES[0]}: {loss16:5.1f}%  '
            f'M2-exemption cost: {exempt:5.2f}%')
    ok = [k for k in knees if k]
    if ok:
        log(f'\nknee across traces: max {max(ok)} entries '
            f'(geomean {math.exp(sum(map(math.log, ok))/len(ok)):.0f})')
    bad = [r for r in rows if r.get('ledger_residual', 0) != 0]
    for r in bad:
        log(f'  LEDGER FAIL {r["trace"]} n={r["size"]} residual {r["ledger_residual"]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=20)
    ap.add_argument('--report-only', action='store_true')
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    budgets = json.load(open(os.path.join(M2, '_budgets.json')))
    jobs = []
    for tr in SELECTED:
        key = tr + SUFFIX
        if key not in budgets:
            log(f'SKIP {tr}: no M2 bind budget')
            continue
        for n in SIZES:
            jobs.append((tr, n, budgets[key]))
    log(f'{len(jobs)} runs ({len(SELECTED)} traces x {len(SIZES)} sizes), concurrency {a.jobs}')

    if not a.report_only:
        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(run_one, *j): j for j in jobs}
            k = 0
            for fu in cf.as_completed(futs):
                k += 1
                try:
                    name, st = fu.result()
                    log(f'  [{k}/{len(jobs)}] {name} {st}')
                except Exception as e:  # noqa: BLE001 - one run must not stop the sweep
                    log(f'  [{k}/{len(jobs)}] {futs[fu][0]} EXC {e}')
    report()
    return 0


if __name__ == '__main__':
    sys.exit(main())
