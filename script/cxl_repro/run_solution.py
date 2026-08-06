#!/usr/bin/env python3
"""[CXLELIDE] solution campaign: {allocate, nt, elide} x {budget off, binding} x real traces.

The question this answers for the paper: does local ownership completion (elide)
take only the good half of each deployed alternative -- nt's transaction count
with allocate's cache residency -- and does that convert into IPC on real
application traces once the link is transaction-limited?

Instruction budget follows the ChampSim literature convention rather than the
sizing pilot: 10M warmup + 50M simulated. Budgets are derived per trace at 40%
of the SLOWEST policy's unconstrained serviced-line rate so they bind for all
three policies.

Usage: run_solution.py run | report
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys

import run_verify as rv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out_solution')
PLAN = os.path.join(OUT, '_budgets.json')
TR = '/home/hwpark/workspace/storage/trace/champsim'
WARMUP = 10_000_000
SIM = 50_000_000
BIND_FRAC = 0.40
WORKERS = 24
POLICIES = ['allocate', 'nt', 'elide']

os.makedirs(OUT, exist_ok=True)
rv.OUT = OUT

TRACES = {
    'bc0':   'gapbs/bc-0.trace.gz',
    'bfs3':  'gapbs/bfs-3.trace.gz',
    'cc5':   'gapbs/cc-5.trace.gz',
    'pr3':   'gapbs/pr-3.trace.gz',
    'sssp3': 'gapbs/sssp-3.trace.gz',
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


def already_done(name):
    p = os.path.join(OUT, name + '.txt')
    if not os.path.exists(p):
        return False
    with open(p) as f:
        return 'ChampSim completed all CPUs' in f.read()


def run_one(name, trace, policy, period_ps):
    if already_done(name):
        return name, 0
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001')
    if period_ps:
        env['CXL_TX_PERIOD_PS'] = str(period_ps)
    else:
        env.pop('CXL_TX_PERIOD_PS', None)
    # No wall-clock timeout. A run under a binding budget is legitimately slow --
    # the modelled machine is throttled, so the same instruction count costs more
    # cycles to simulate, and a 20% budget costs roughly five times a 40% one. A
    # timeout here does not catch a bug, it throws away hours of finished work.
    with open(os.path.join(OUT, name + '.txt'), 'w') as f:
        p = subprocess.run(
            [rv.BIN, '--warmup-instructions', str(WARMUP),
             '--simulation-instructions', str(SIM), os.path.join(TR, trace)],
            env=env, stdout=f, stderr=subprocess.STDOUT)
    return name, p.returncode


def stats(name):
    d = rv.parse(name)
    text = open(os.path.join(OUT, name + '.txt')).read()
    m = re.search(r'FAR_CHANNEL_0 TX_GRANTS:\s+(\d+)', text)
    d['tx_grants'] = int(m.group(1)) if m else 0
    m = re.search(r'CXLELIDE elided_grants:\s+(\d+)', text)
    d['elided'] = int(m.group(1)) if m else 0
    if d.get('roi_sec') and d.get('far'):
        d['line_rate'] = (d['far'].get('rd_lines', 0) + d['far'].get('wr_lines', 0)) / d['roi_sec']
        useful_wr = d['nt_bypass'] if d['nt_bypass'] > 0 else d['wb']
        d['useful_lines'] = d['demand_rd'] + useful_wr
        lines = d['far'].get('rd_lines', 0) + d['far'].get('wr_lines', 0)
        d['txpl'] = lines / d['useful_lines'] if d['useful_lines'] else 0.0
    return d


def wave(jobs):
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(run_one, *j): j[0] for j in jobs}
        for fu in cf.as_completed(futs):
            try:
                print('[run] %-24s rc=%d' % fu.result(), flush=True)
            except Exception as e:
                print('[run] %-24s EXC %s' % (futs[fu], e), flush=True)


def budget_for(k):
    """A trace's budget depends only on that trace's own baselines."""
    rates = []
    for p in POLICIES:
        try:
            st = stats('%s_%s_off' % (k, p))
        except FileNotFoundError:
            return None
        if st.get('line_rate'):
            rates.append(st['line_rate'])
    if len(rates) < len(POLICIES):
        return None
    return max(1, int(round(1e12 / (BIND_FRAC * min(rates)))))


def _policies_concurrently(names_args):
    with cf.ThreadPoolExecutor(max_workers=len(POLICIES)) as ex:
        for fu in cf.as_completed({ex.submit(run_one, *a) for a in names_args}):
            try:
                print('[run] %-24s rc=%d' % fu.result(), flush=True)
            except Exception as e:
                print('[run] EXC %s' % e, flush=True)


def do_trace(k, trace):
    """Baselines then binding runs for one trace -- no cross-trace barrier, so a
    slow trace delays only itself. The three policies run concurrently within
    each stage, so the pool width is (traces in flight) x 3."""
    _policies_concurrently([('%s_%s_off' % (k, p), trace, p, 0) for p in POLICIES])
    b = budget_for(k)
    if b is None:
        print('[plan] %-8s SKIP (incomplete baselines)' % k, flush=True)
        return k, None
    print('[plan] %-8s period %d ps' % (k, b), flush=True)
    _policies_concurrently([('%s_%s_bind' % (k, p), trace, p, b) for p in POLICIES])
    return k, b


def do_run():
    print('[campaign] %d traces x %d policies x 2 modes, pipelined per trace'
          % (len(TRACES), len(POLICIES)), flush=True)
    budgets = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, WORKERS // len(POLICIES))) as ex:
        futs = {ex.submit(do_trace, k, t): k for k, t in TRACES.items()}
        for fu in cf.as_completed(futs):
            try:
                k, b = fu.result()
                if b:
                    budgets[k] = b
            except Exception as e:
                print('[campaign] %-8s EXC %s' % (futs[fu], e), flush=True)
            json.dump(budgets, open(PLAN, 'w'), indent=1)
    json.dump(budgets, open(PLAN, 'w'), indent=1)
    print('RUN_DONE', flush=True)


def do_report():
    budgets = json.load(open(PLAN))
    hdr = ('trace', 'IPCa/o', 'IPCn/o', 'IPCe/o', 'IPCa/b', 'IPCn/b', 'IPCe/b',
           'e/a off', 'e/a bnd', 'e/n bnd', 'txpl e')
    print(('%-9s' + '%8s' * 10) % hdr)
    for k in TRACES:
        try:
            S = {(p, m): stats('%s_%s_%s' % (k, p, m)) for p in POLICIES for m in ('off', 'bind')}
        except FileNotFoundError:
            print('%-9s missing' % k)
            continue
        if not all(s['completed'] for s in S.values()):
            print('%-9s incomplete' % k)
            continue
        g = lambda p, m, f='ipc': S[(p, m)].get(f, 0)
        r = lambda a, b: a / b if b else float('nan')
        print(('%-9s' + '%8.4f' * 6 + '%8.3f%8.3f%8.3f%8.3f') %
              (k, g('allocate', 'off'), g('nt', 'off'), g('elide', 'off'),
               g('allocate', 'bind'), g('nt', 'bind'), g('elide', 'bind'),
               r(g('elide', 'off'), g('allocate', 'off')),
               r(g('elide', 'bind'), g('allocate', 'bind')),
               r(g('elide', 'bind'), g('nt', 'bind')),
               S[('elide', 'bind')].get('txpl', 0)))


if __name__ == '__main__':
    {'run': do_run, 'report': do_report}[sys.argv[1] if len(sys.argv) > 1 else 'report']()
