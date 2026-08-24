#!/usr/bin/env python3
"""M2: four policies over the clean v2 corpus, budget off and budget bound.

This is the campaign the blueprint (elid_blueprint.md 12.1) calls M2, run on 644_m5
against v2_final -- the 85-slice corpus rebuilt after the tracer overflow (context.md
18.16).  It answers what the coverage number alone cannot: how much of the unsafe
`elide` upper bound the realistic policies keep, in IPC and in link accounting.

Methodology follows run_solution.py so the numbers remain comparable: warmup 10M +
sim 50M, per-trace binding budget at BIND_FRAC of the slowest policy's own line rate,
policies concurrent within a stage, traces pipelined with no cross-trace barrier.
Differences from run_solution.py, all deliberate:
  - four policies: allocate / elide / elide_safe / elide_fetch (nt was settled in 16.4)
  - --size-trace: v2 records carry extents; without the flag every record misparses
    and the failure mode is a deadlock dump, not an error message (18.10 gate lesson)
  - coverage stats (M1) fall out of the elide runs for free; one parse serves both

A run is resumable: a finished output file is left alone, so the campaign can be
restarted after an interruption without redoing hours of work.
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
OUT = '/mnt/local_storage/m2_campaign'

WARMUP = 10_000_000
SIM = 50_000_000
POLICIES = ['allocate', 'elide', 'elide_safe', 'elide_fetch']
BIND_FRAC = 0.40
CPU_PERIOD_PS = 250  # 4 GHz core
LINE = 64

MASK_FIELDS = [
    'episodes_opened', 'episodes_closed', 'closed_full', 'closed_partial',
    'closed_read_uncov', 'closed_irregular', 'evicted_by_conflict',
    'stores_tracked', 'stores_unsized', 'straddling_stores', 'live_max', 'live_now',
    'full_on_first_store', 'partial_to_full', 'partial_writebacks', 'merge_fetches',
    'regrant_on_open', 'untracked_far_wb', 'fetch_on_open_ep', 'subword_episodes',
]


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def out_path(trace, policy, mode):
    return os.path.join(OUT, f'{trace}__{policy}__{mode}.txt')


def already_done(path):
    if not os.path.exists(path):
        return False
    with open(path) as f:
        return 'ChampSim completed all CPUs' in f.read()


def terminally_failed(path):
    """A failure that reruns identically: retrying burns a slot and changes nothing.
    Only the vmem page-pool exhaustion qualifies -- it is a property of the trace's
    footprint against the configured 8 GiB far pool, not of the run (ctx 18.6 saw the
    same wall; 18.9's 8 GiB was not enough for cactuBSSN)."""
    if not os.path.exists(path):
        return False
    with open(path) as f:
        return 'available_ppages' in f.read()


def run_one(trace, policy, mode, period_ps):
    path = out_path(trace, policy, mode)
    if already_done(path):
        return f'{trace} {policy} {mode}', 'cached'
    if terminally_failed(path):
        return f'{trace} {policy} {mode}', 'vmem-excluded'
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001')
    if period_ps:
        env['CXL_TX_PERIOD_PS'] = str(period_ps)
    else:
        env.pop('CXL_TX_PERIOD_PS', None)
    cmd = [BIN, '--size-trace', '--warmup-instructions', str(WARMUP),
           '--simulation-instructions', str(SIM), os.path.join(TRACES, trace)]
    # No wall-clock timeout: a budget-bound run is legitimately slow -- the modelled
    # machine is throttled, so the same instruction count costs more cycles to simulate.
    with open(path, 'w') as f:
        p = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    return f'{trace} {policy} {mode}', f'rc={p.returncode}'


def parse(trace, policy, mode):
    path = out_path(trace, policy, mode)
    if not os.path.exists(path):
        return None
    text = open(path).read()
    d = {'trace': trace, 'policy': policy, 'mode': mode,
         'completed': 'ChampSim completed all CPUs' in text}
    m = re.findall(r'CPU 0 cumulative IPC: ([\d.]+) instructions: (\d+) cycles: (\d+)', text)
    if m:
        ipc, _, cycles = m[-1]
        d['ipc'], d['cycles'] = float(ipc), int(cycles)
        d['roi_sec'] = int(cycles) * CPU_PERIOD_PS * 1e-12
    cm = re.search(r'FAR_CHANNEL_0 RD_LINES:\s+(\d+) WR_LINES:\s+(\d+)'
                   r' RD_BUS_BUSY_ps:\s+(\d+) WR_BUS_BUSY_ps:\s+(\d+)', text)
    if cm:
        d['rd_lines'], d['wr_lines'], d['rd_busy_ps'], d['wr_busy_ps'] = map(int, cm.groups())
    gm = re.search(r'FAR_CHANNEL_0 TX_GRANTS:\s+(\d+)', text)
    if gm:
        d['tx_grants'] = int(gm.group(1))
    pm = re.search(r'FAR_CHANNEL_0.*PARTIAL_WRITE_LINES:\s+(\d+)', text)
    if pm:
        d['partial_write_lines'] = int(pm.group(1))
    for key, pat in [('rfo_fetch', r'far_rfo_fetches:\s+(\d+)'),
                     ('demand_rd', r'far_demand_reads:\s+(\d+)'),
                     ('wb', r'far_writebacks:\s+(\d+)'),
                     ('elided', r'elided_grants:\s+(\d+)'),
                     ('wq_backlog', r'far_wq_backlog:\s+(\d+)')]:
        mm = re.search(pat, text)
        d[key] = int(mm.group(1)) if mm else 0
    for f in MASK_FIELDS:
        mm = re.search(rf'CXLMASK {f}: *(\d+)', text)
        if mm:
            d[f] = int(mm.group(1))
    hm = re.search(r'CXLMASK covered_hist[^:]*: *((?:\d+ *)+)', text)
    if hm:
        d['covered_hist'] = [int(x) for x in hm.group(1).split()]
    wm = re.search(r'CXLMASK covered_words_hist[^:]*: *((?:\d+ *)+)', text)
    if wm:
        d['covered_words_hist'] = [int(x) for x in wm.group(1).split()]
    # Episode ledger (critic F6/F14): an unexplained residual means episodes vanished
    # silently, in the direction that flatters the proposal.  Flag it, don't average it.
    if all(k in d for k in ('episodes_opened', 'episodes_closed', 'evicted_by_conflict', 'live_now')):
        d['ledger_residual'] = (d['episodes_opened'] - d['episodes_closed']
                                - d['evicted_by_conflict'] - d['live_now'])
        if d['ledger_residual'] != 0:
            d['invalid'] = f"ledger residual {d['ledger_residual']}"
    if d.get('roi_sec') and 'rd_lines' in d:
        d['line_rate'] = (d['rd_lines'] + d['wr_lines']) / d['roi_sec']
    if d.get('episodes_closed'):
        d['full_fraction'] = d.get('closed_full', 0) / d['episodes_closed']
    # A hook that never fires reports zero coverage that looks like real coverage
    # (18.3.4); an unsized store means the extents are not reaching the model.
    if policy != 'allocate':
        if d.get('episodes_opened', 0) > 0 and d.get('stores_tracked', 0) == 0:
            d['invalid'] = 'no stores observed on tracked lines'
        if d.get('stores_unsized', 0) > 0:
            d['invalid'] = f"{d['stores_unsized']} unsized stores"
    return d


# A budget period above this is degenerate: it means the trace's own far line rate is
# so low that throttling it to 40% starves single misses past the deadlock detector
# (gcc_r-167B and exchange2_r died this way at periods ~1e7 ps).  Such a trace gets
# nothing from bind mode -- the budget never contends -- so bind is skipped, not fixed.
BIND_CAP_PS = 1_000_000


def budget_for(trace):
    """The binding budget depends only on this trace's own budget-off baselines.
    If any bind run already completed (a resumed campaign), reuse the period it ran
    with -- recomputing from rerun baselines would mix budgets within one trace."""
    for p in POLICIES:
        path = out_path(trace, p, 'bind')
        if already_done(path):
            m = re.search(r'CXLTX tx_period_ps:\s+(\d+)', open(path).read())
            if m and int(m.group(1)) > 0:
                return int(m.group(1))
    rates = []
    for p in POLICIES:
        d = parse(trace, p, 'off')
        if d is None or not d.get('completed') or not d.get('line_rate'):
            return None
        rates.append(d['line_rate'])
    return max(1, int(round(1e12 / (BIND_FRAC * min(rates)))))


def do_trace(trace):
    """Budget-off for all four policies, then this trace's binding budget, then
    budget-bound for all four.  No cross-trace barrier: a slow trace delays itself."""
    with cf.ThreadPoolExecutor(max_workers=len(POLICIES)) as ex:
        for fu in cf.as_completed([ex.submit(run_one, trace, p, 'off', 0) for p in POLICIES]):
            log(f'  [off ] {fu.result()[0]} {fu.result()[1]}')
    b = budget_for(trace)
    if b is None:
        log(f'  [plan] {trace} SKIP bind (incomplete baselines)')
        return trace, None
    if b > BIND_CAP_PS:
        log(f'  [plan] {trace} SKIP bind (degenerate period {b} ps > cap {BIND_CAP_PS})')
        return trace, None
    log(f'  [plan] {trace} period {b} ps')
    with cf.ThreadPoolExecutor(max_workers=len(POLICIES)) as ex:
        for fu in cf.as_completed([ex.submit(run_one, trace, p, 'bind', b) for p in POLICIES]):
            log(f'  [bind] {fu.result()[0]} {fu.result()[1]}')
    return trace, b


def report(names):
    rows = [d for t in names for p in POLICIES for m in ('off', 'bind')
            if (d := parse(t, p, m)) is not None]
    with open(os.path.join(OUT, '_m2.json'), 'w') as f:
        json.dump(rows, f, indent=1)
    log(f'{len(rows)} runs parsed -> _m2.json')

    by = {(r['trace'], r['policy'], r['mode']): r for r in rows}
    log('\ntrace                                     mode  IPC a/e/es/ef            es/e     ef/e     full%')
    geo = {p: [] for p in POLICIES}
    for t in names:
        for mode in ('off', 'bind'):
            vals = [by.get((t, p, mode)) for p in POLICIES]
            if any(v is None or not v.get('completed') or 'ipc' not in v for v in vals):
                continue
            a, e, es, ef = (v['ipc'] for v in vals)
            cov = by.get((t, 'elide', mode), {}).get('full_fraction')
            covs = f'{cov*100:5.1f}' if cov is not None else '    -'
            log(f'{t[:40]:42s} {mode:4s} {a:5.3f}/{e:5.3f}/{es:5.3f}/{ef:5.3f}  '
                f'{es/e if e else 0:7.4f}  {ef/e if e else 0:7.4f}  {covs}')
            if mode == 'bind' and a > 0:
                for p, v in zip(POLICIES, vals):
                    geo[p].append(v['ipc'] / a)
    import math
    gm = {p: math.exp(sum(math.log(x) for x in v) / len(v)) if v else None for p, v in geo.items()}
    log(f'\ngeomean IPC vs allocate (bind, n={len(geo["elide"])}): '
        + '  '.join(f'{p}={gm[p]:.4f}' if gm[p] else f'{p}=-' for p in POLICIES))

    bad = [r for r in rows if 'invalid' in r]
    for r in bad:
        log(f"  INVALID {r['trace']} {r['policy']} {r['mode']}: {r['invalid']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trace-jobs', type=int, default=7,
                    help='traces in flight; pool width = this x 4 policies')
    ap.add_argument('--report-only', action='store_true')
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    names = sorted(f for f in os.listdir(TRACES) if f.endswith('.xz'))
    log(f'{len(names)} traces x {len(POLICIES)} policies x 2 modes = {len(names)*8} runs, '
        f'{a.trace_jobs} traces in flight')

    if not a.report_only:
        budgets = {}
        with cf.ThreadPoolExecutor(max_workers=a.trace_jobs) as ex:
            futs = {ex.submit(do_trace, t): t for t in names}
            done = 0
            for fu in cf.as_completed(futs):
                done += 1
                try:
                    t, b = fu.result()
                    if b:
                        budgets[t] = b
                    log(f'[{done}/{len(names)}] {t} done')
                except Exception as exc:  # noqa: BLE001 - one trace must not stop the sweep
                    log(f'[{done}/{len(names)}] {futs[fu]} EXC {exc}')
        with open(os.path.join(OUT, '_budgets.json'), 'w') as f:
            json.dump(budgets, f, indent=1)

    report(names)
    return 0


if __name__ == '__main__':
    sys.exit(main())
