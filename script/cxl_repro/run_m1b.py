#!/usr/bin/env python3
"""M1b: settle what live_max actually measures, and what the subword flag actually costs.

Two questions left open by M2/M3, both answered by counters added 2026-08-19:

1. `live_max` was read as "partial lines resident in the LLC" and used to size a
   hypothetical per-LLC side table. It is not that quantity. An elide grant fills the LLC
   line clean (the LLC sees RFO, and fill_block sets dirty only for WRITE), so a clean
   victim never reaches the far writeback and mask_close never fires -- the episode stays
   counted while its dirty bytes sit upstream. `llc_resident_max` measures LLC occupancy
   directly, as a +1/-1 at the fill commit point.

2. `subword_episodes` is sticky: one misaligned store marks the whole episode. An episode
   that saw one and still closed full costs nothing. `closed_partial_subword` is the
   intersection, which is the figure the 4 B-granularity decision actually rests on.

Two stages, because the two questions want different conditions. Coverage is a property of
the trace, so stage A runs budget-off over the whole corpus. LLC residency is a timing
property, so stage B reruns the ten M3 slices under their own M3 budget, where the numbers
are directly comparable to the live_max the sweep reported.
"""

import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.abspath(os.path.join(HERE, '..', '..', 'bin', 'champsim_cxl_repro'))
TRACES = '/mnt/local_storage/trace/champsim/v2_final'
M2 = '/mnt/local_storage/m2_campaign'
OUT = '/mnt/local_storage/m1b'
WARMUP, SIM = 10_000_000, 50_000_000
SUFFIX = '.champsimtrace.xz'

M3_SLICES = ['519.lbm_r-413B', '505.mcf_r-542B', '520.omnetpp_r-104B', '505.mcf_r-197B',
             '508.namd_r-750B', '521.wrf_r-1957B', '523.xalancbmk_r-520B',
             '554.roms_r-172B', '531.deepsjeng_r-614B', '503.bwaves_r-34B']

FIELDS = ['episodes_opened', 'episodes_closed', 'closed_full', 'closed_partial',
          'evicted_by_conflict', 'live_now', 'live_max', 'llc_resident_max',
          'llc_resident_now', 'llc_clean_drops', 'subword_episodes',
          'closed_partial_sub', 'stores_unsized', 'regrant_on_open']


def log(m): print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def path_for(trace, stage):
    return os.path.join(OUT, f'{trace}__{stage}.txt')


def run_one(trace, stage, policy, period):
    p = path_for(trace, stage)
    if os.path.exists(p) and 'ChampSim completed all CPUs' in open(p).read():
        return f'{trace} {stage}', 'cached'
    if os.path.exists(p) and 'available_ppages' in open(p).read():
        return f'{trace} {stage}', 'vmem-excluded'
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001')
    if period:
        env['CXL_TX_PERIOD_PS'] = str(period)
    cmd = [BIN, '--size-trace', '--warmup-instructions', str(WARMUP),
           '--simulation-instructions', str(SIM), os.path.join(TRACES, trace + SUFFIX)]
    with open(p, 'w') as f:
        r = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    return f'{trace} {stage}', f'rc={r.returncode}'


def parse(trace, stage):
    p = path_for(trace, stage)
    if not os.path.exists(p):
        return None
    t = open(p).read()
    if 'ChampSim completed all CPUs' not in t:
        return None
    d = {'trace': trace, 'stage': stage}
    for k in FIELDS:
        m = re.search(rf'CXLMASK {k}: *(\d+)', t)
        if m:
            d[k] = int(m.group(1))
    return d


def report(names):
    rows = [r for n in names for s in ('A', 'B') if (r := parse(n, s))]
    with open(os.path.join(OUT, '_m1b.json'), 'w') as f:
        json.dump(rows, f, indent=1)
    log(f'{len(rows)} runs parsed\n')

    log('=== Q1: live_max vs llc_resident_max (stage B = M3 조건)')
    log(f'{"trace":22s} {"live_max":>9s} {"llc_res_max":>11s} {"비율":>7s} {"clean_drops":>11s}')
    for r in [x for x in rows if x['stage'] == 'B']:
        lm, lr = r.get('live_max', 0), r.get('llc_resident_max', 0)
        log(f'{r["trace"][:22]:22s} {lm:9d} {lr:11d} {lr/lm if lm else 0:7.3f} {r.get("llc_clean_drops",0):11d}')

    log('\n=== Q2: subword sticky vs 실제 교집합 (stage A = 전 코퍼스)')
    A = [x for x in rows if x['stage'] == 'A' and x.get('episodes_closed', 0) >= 10000]
    tc = sum(x['episodes_closed'] for x in A)
    sub = sum(x.get('subword_episodes', 0) for x in A)
    inter = sum(x.get('closed_partial_sub', 0) for x in A)
    if tc:
        log(f'  에피소드 {tc:,} 중  sticky {sub:,} ({100.0*sub/tc:.2f}%)  '
            f'교집합 {inter:,} ({100.0*inter/tc:.2f}%)  → 과대평가 {sub/inter if inter else float("inf"):.2f}배')
    log(f'  pooled 덮임 재확인: {sum(x.get("closed_full",0) for x in A)}/{tc} = '
        f'{100.0*sum(x.get("closed_full",0) for x in A)/tc:.2f}%' if tc else '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=20)
    ap.add_argument('--report-only', action='store_true')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    budgets = json.load(open(os.path.join(M2, '_budgets.json')))
    names = sorted(f[:-len(SUFFIX)] for f in os.listdir(TRACES) if f.endswith(SUFFIX))

    jobs = [(n, 'A', 'elide', 0) for n in names]
    jobs += [(s, 'B', 'elide_safe', budgets[s + SUFFIX])
             for s in M3_SLICES if s + SUFFIX in budgets]
    log(f'{len(jobs)} runs (A: {len(names)} 전 코퍼스 / B: M3 10구간), 동시성 {a.jobs}')

    if not a.report_only:
        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(run_one, *j): j for j in jobs}
            k = 0
            for fu in cf.as_completed(futs):
                k += 1
                try:
                    n, st = fu.result()
                    log(f'  [{k}/{len(jobs)}] {n} {st}')
                except Exception as e:  # noqa: BLE001
                    log(f'  [{k}/{len(jobs)}] {futs[fu][0]} EXC {e}')
    report(names)
    return 0


if __name__ == '__main__':
    sys.exit(main())
