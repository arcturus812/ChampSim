#!/usr/bin/env python3
"""[CXLTX] real-trace validation -- context.md sec 10.6, the four-step plan.

The synthetic-trace result to beat: with the transaction budget binding, useful
throughput converged to the pure per-line transaction ratio (predicted 1.500,
measured 1.500 -- run_verify_tx.py T9).  That ratio was trivial by construction.
Real traces have reuse, partial-line stores and non-trivial read/write mixes, so
tx-per-useful-line is a measured, workload-specific number here; whether it still
predicts the throughput ratio under a binding budget is the actual test (S3).

Steps mapped to context.md sec 10.6:
  S1  budget OFF, real traces: model runs, far traffic present        (step 1)
  S2  budget binding: neither bus direction saturated, rate == budget (step 2)
  S3  nt/alloc useful ratio == txpl_alloc/txpl_nt from the bound runs (step 3)
  far traffic is forced with CXL_ALLOC_POLICY=only_far                (step 4)

Budgets are derived per trace from the unconstrained baselines (wave 1), at 40%
of the slower policy's serviced-line rate so the budget binds for BOTH policies.

Usage:  run_realtrace.py run | report
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys

import run_verify as rv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out_real')
PLAN = os.path.join(OUT, '_budgets.json')
TR = '/home/hwpark/workspace/storage/trace/champsim'
WARMUP = 2_000_000
SIM = 10_000_000
BIND_FRAC = 0.40
WORKERS = 24

os.makedirs(OUT, exist_ok=True)
rv.OUT = OUT  # rv.parse() reads from here

TRACES = {
    # GAPBS (one slice per kernel; tc has no trace in this set)
    'bc0':   'gapbs/bc-0.trace.gz',
    'bfs3':  'gapbs/bfs-3.trace.gz',
    'cc5':   'gapbs/cc-5.trace.gz',
    'pr3':   'gapbs/pr-3.trace.gz',
    'sssp3': 'gapbs/sssp-3.trace.gz',
    # SPEC CPU 2017 rate/speed slices, s0 of every benchmark present
    'gcc':      'spec17/602.gcc-s0.trace.xz',
    'mcf':      'spec17/605.mcf-s0.trace.xz',
    'cactu':    'spec17/607.cactuBSSN-s0.trace.xz',
    'lbm':      'spec17/619.lbm-s0.trace.xz',
    'omnetpp':  'spec17/620.omnetpp-s0.trace.xz',
    'wrf':      'spec17/621.wrf-s0.trace.xz',
    'xalanc':   'spec17/623.xalancbmk-s0.trace.xz',
    'fotonik':  'spec17/649.fotonik3d-s0.trace.xz',
    'roms':     'spec17/654.roms-s0.trace.xz',
}


def run_one(name, trace, policy, period_ps):
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001')
    if period_ps:
        env['CXL_TX_PERIOD_PS'] = str(period_ps)
    else:
        env.pop('CXL_TX_PERIOD_PS', None)
    env.pop('CXL_TX_RATE_MTPS', None)
    with open(os.path.join(OUT, name + '.txt'), 'w') as f:
        p = subprocess.run(
            [rv.BIN, '--warmup-instructions', str(WARMUP),
             '--simulation-instructions', str(SIM), os.path.join(TR, trace)],
            env=env, stdout=f, stderr=subprocess.STDOUT)
    return name, p.returncode


def stats(name):
    d = rv.parse(name)
    text = open(os.path.join(OUT, name + '.txt')).read()
    m = re.search(r'FAR_CHANNEL_0 TX_GRANTS:\s+(\d+) TX_STALL_EVENTS:\s+(\d+)', text)
    d['tx_grants'] = int(m.group(1)) if m else 0
    if d.get('roi_sec') and d.get('far'):
        d['line_rate'] = (d['far'].get('rd_lines', 0) + d['far'].get('wr_lines', 0)) / d['roi_sec']
        d['tx_rate'] = d['tx_grants'] / d['roi_sec']
        useful_wr = d['nt_bypass'] if d['nt_bypass'] > 0 else d['wb']
        d['useful_lines'] = d['demand_rd'] + useful_wr
        # per-line transaction cost, from the run's own accounting
        lines = d['far'].get('rd_lines', 0) + d['far'].get('wr_lines', 0)
        d['txpl'] = lines / d['useful_lines'] if d['useful_lines'] else 0.0
    return d


def wave(jobs):
    bad = []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(run_one, *j): j[0] for j in jobs}
        for fu in cf.as_completed(futs):
            n, rc = fu.result()
            print('[run] %-22s rc=%d' % (n, rc), flush=True)
            if rc != 0:
                bad.append(n)
    return bad


def do_run():
    print('[wave1] %d baseline runs (budget off)' % (2 * len(TRACES)), flush=True)
    wave([(k + '_alloc_off', t, 'allocate', 0) for k, t in TRACES.items()] +
         [(k + '_nt_off', t, 'nt', 0) for k, t in TRACES.items()])

    budgets = {}
    for k in TRACES:
        a, n = stats(k + '_alloc_off'), stats(k + '_nt_off')
        if not (a.get('line_rate') and n.get('line_rate')):
            print('[plan] %-8s SKIP (missing baseline)' % k, flush=True)
            continue
        lr = min(a['line_rate'], n['line_rate'])
        budgets[k] = max(1, int(round(1e12 / (BIND_FRAC * lr))))
        print('[plan] %-8s alloc %.3e nt %.3e lines/s -> period %d ps'
              % (k, a['line_rate'], n['line_rate'], budgets[k]), flush=True)
    json.dump(budgets, open(PLAN, 'w'), indent=1)

    print('[wave2] %d binding runs' % (2 * len(budgets)), flush=True)
    wave([(k + '_alloc_bind', TRACES[k], 'allocate', budgets[k]) for k in budgets] +
         [(k + '_nt_bind', TRACES[k], 'nt', budgets[k]) for k in budgets])
    print('RUN_DONE', flush=True)


def do_report():
    budgets = json.load(open(PLAN))
    hdr = ('trace', 'txpl_a', 'txpl_n', 'pred', 'meas', 'err%', 'rdBusy', 'wrBusy', 'tx/bud', 'S2', 'S3')
    print(('%-9s' + '%8s' * 10) % hdr)
    s1 = s2ok = s3ok = graded = 0
    for k in budgets:
        try:
            a, n = stats(k + '_alloc_bind'), stats(k + '_nt_bind')
        except FileNotFoundError:
            print('%-9s missing' % k)
            continue
        if not (a['completed'] and n['completed'] and a.get('useful_lines')):
            print('%-9s incomplete' % k)
            continue
        s1 += 1
        budget_rate = 1e12 / budgets[k]
        bound_a = abs(a['tx_rate'] - budget_rate) / budget_rate < 0.05
        bound_n = abs(n['tx_rate'] - budget_rate) / budget_rate < 0.05
        # S2: budget binds while neither direction is saturated
        s2 = bound_a and a['rd_busy_frac'] < 0.95 and a['wr_busy_frac'] < 0.95
        # S3: per-line transaction accounting predicts the throughput ratio
        pred = a['txpl'] / n['txpl'] if n['txpl'] else 0.0
        meas = n['useful_gbs'] / a['useful_gbs'] if a['useful_gbs'] else 0.0
        err = 100 * (meas - pred) / pred if pred else float('nan')
        s3 = bound_a and bound_n and pred and abs(err) <= 10
        s2ok += s2
        if bound_a and bound_n:
            graded += 1
            s3ok += bool(s3)
        print(('%-9s' + '%8.3f' * 5 + '%8.2f%8.2f%8.2f' + '%8s%8s')
              % (k, a['txpl'], n['txpl'], pred, meas, err,
                 a['rd_busy_frac'], a['wr_busy_frac'], a['tx_rate'] / budget_rate,
                 'ok' if s2 else 'FAIL', ('ok' if s3 else 'FAIL') if bound_a and bound_n else 'unbound'))
    print('\nS1 completed pairs: %d/%d   S2 signature: %d   S3 accounting (of %d graded): %d'
          % (s1, len(budgets), s2ok, graded, s3ok))


if __name__ == '__main__':
    {'run': do_run, 'report': do_report}[sys.argv[1] if len(sys.argv) > 1 else 'report']()
