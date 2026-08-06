#!/usr/bin/env python3
"""[CXLELIDE] auxiliary campaign -- fills the cores the main solution campaign leaves idle.

Unlike the real-machine measurements, simulator results are deterministic: CPU
contention costs wall-clock, never correctness. This queue therefore runs
alongside run_solution.py under `nice`, so the main campaign keeps priority and
the machine's spare capacity is not wasted during its barrier tails.

Phase A -- reuse-distance sweep, three policies.
  The paper's R2 requires a fix to occupy BOTH halves of the write-to-reread
  sweep: allocate wins while the line is still cached, nt wins once it is not.
  Only elide should hold both. Synthetic traces put the reuse distance under
  direct control, with the model's 2 MiB LLC as the crossing point.

Phase B -- budget tightness.
  The main campaign binds every trace at 40% of its unconstrained line rate.
  A reviewer will ask whether the elide gain is an artifact of that number, so
  this repeats representative traces at 20% (twice the period) for a third
  point on the curve; budget-off from the main campaign is the first.

Usage: run_aux.py run | report
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time

import run_verify as rv
import run_solution as sol

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out_aux')
TR = sol.TR
POLICIES = ['allocate', 'nt', 'elide']
WORKERS = 12
NICE = 10

# model LLC is 2048 sets x 16 ways x 64 B = 2 MiB = 32768 lines; bracket it
REUSE_LINES = [1024, 4096, 16384, 32768, 65536, 262144]
BUF_MIB = 32                       # 16x the LLC, so the buffer never fits
TIGHT_TRACES = ['lbm', 'bc0', 'mcf', 'cactu']
TIGHT_FRAC = 0.20                  # main campaign uses 0.40

os.makedirs(OUT, exist_ok=True)
rv.OUT = OUT



def already_done(name, outdir):
    p = os.path.join(outdir, name + '.txt')
    if not os.path.exists(p):
        return False
    with open(p) as f:
        return 'ChampSim completed all CPUs' in f.read()

def run_one(name, trace_path, policy, period_ps, warmup, sim):
    if already_done(name, OUT):
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
            ['nice', '-n', str(NICE), rv.BIN, '--warmup-instructions', str(warmup),
             '--simulation-instructions', str(sim), trace_path],
            env=env, stdout=f, stderr=subprocess.STDOUT)
    return name, p.returncode


def stats(name):
    d = rv.parse(name)
    text = open(os.path.join(OUT, name + '.txt')).read()
    m = re.search(r'CXLELIDE elided_grants:\s+(\d+)', text)
    d['elided'] = int(m.group(1)) if m else 0
    if d.get('roi_sec') and d.get('far'):
        useful_wr = d['nt_bypass'] if d['nt_bypass'] > 0 else d['wb']
        d['useful_lines'] = d['demand_rd'] + useful_wr
        lines = d['far'].get('rd_lines', 0) + d['far'].get('wr_lines', 0)
        d['txpl'] = lines / d['useful_lines'] if d['useful_lines'] else 0.0
    return d


def wave(jobs):
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fu in cf.as_completed({ex.submit(run_one, *j) for j in jobs}):
            print('[aux] %-26s rc=%d' % fu.result(), flush=True)


def gen_traces():
    """Synthetic pure-store traces with a controlled write-to-reread distance."""
    tg = os.path.join(HERE, 'tracegen')
    subprocess.run(['gcc', '-O2', '-o', tg, os.path.join(HERE, 'tracegen.c')], check=True)
    made = {}
    for d in REUSE_LINES:
        path = os.path.join(rv.TRACES, 'aux_reuse_%d.champsim.gz' % d)
        if not os.path.exists(path):
            with open(path, 'wb') as f:
                gen = subprocess.Popen([tg, '0', str(BUF_MIB), str(d)], stdout=subprocess.PIPE)
                gz = subprocess.Popen(['gzip', '-1'], stdin=gen.stdout, stdout=f)
                gen.stdout.close()
                gz.communicate()
        made[d] = path
        print('[aux] trace ready: reuse=%d lines' % d, flush=True)
    return made


def do_run():
    print('[phase A] reuse sweep: %d distances x %d policies' %
          (len(REUSE_LINES), len(POLICIES)), flush=True)
    traces = gen_traces()
    wave([('reuse%d_%s' % (d, p), traces[d], p, 0, rv.WARMUP, rv.SIM)
          for d in REUSE_LINES for p in POLICIES])

    # Phase B needs the main campaign's derived budgets.
    plan_path = sol.PLAN
    for _ in range(360):
        if os.path.exists(plan_path):
            break
        print('[phase B] waiting for the main campaign to derive budgets ...', flush=True)
        time.sleep(120)
    if not os.path.exists(plan_path):
        print('[phase B] SKIPPED: no budget plan after 12 h', flush=True)
        print('AUX_DONE', flush=True)
        return
    main_budgets = json.load(open(plan_path))

    jobs = []
    for k in TIGHT_TRACES:
        if k not in main_budgets:
            print('[phase B] %s not in the main plan, skipping' % k, flush=True)
            continue
        # main period is at 0.40 of the slowest rate; 0.20 doubles the period
        period = int(round(main_budgets[k] * 0.40 / TIGHT_FRAC))
        print('[phase B] %-8s tight period %d ps (main %d)' % (k, period, main_budgets[k]),
              flush=True)
        for p in POLICIES:
            jobs.append(('%s_%s_tight' % (k, p), os.path.join(TR, sol.TRACES[k]), p, period,
                         sol.WARMUP, sol.SIM))
    print('[phase B] %d runs' % len(jobs), flush=True)
    wave(jobs)
    print('AUX_DONE', flush=True)


def do_report():
    print('== Phase A: reuse-distance sweep (budget off), useful GB/s and IPC')
    print('%10s %10s %10s %10s | %8s %8s %8s' %
          ('reuse KiB', 'alloc', 'nt', 'elide', 'IPC a', 'IPC n', 'IPC e'))
    for d in REUSE_LINES:
        try:
            S = {p: stats('reuse%d_%s' % (d, p)) for p in POLICIES}
        except FileNotFoundError:
            continue
        if not all(s['completed'] for s in S.values()):
            print('%10d incomplete' % (d * 64 // 1024))
            continue
        print('%10d %10.2f %10.2f %10.2f | %8.4f %8.4f %8.4f' %
              (d * 64 // 1024, S['allocate']['useful_gbs'], S['nt']['useful_gbs'],
               S['elide']['useful_gbs'], S['allocate']['ipc'], S['nt']['ipc'],
               S['elide']['ipc']))

    print('\n== Phase B: tighter budget (20% of baseline rate)')
    print('%-9s %8s %8s %8s %8s' % ('trace', 'IPC a', 'IPC n', 'IPC e', 'e/a'))
    for k in TIGHT_TRACES:
        try:
            S = {p: stats('%s_%s_tight' % (k, p)) for p in POLICIES}
        except FileNotFoundError:
            continue
        if not all(s['completed'] for s in S.values()):
            print('%-9s incomplete' % k)
            continue
        a, n, e = (S[p].get('ipc', 0) for p in POLICIES)
        print('%-9s %8.4f %8.4f %8.4f %8.3f' % (k, a, n, e, e / a if a else float('nan')))


if __name__ == '__main__':
    {'run': do_run, 'report': do_report}[sys.argv[1] if len(sys.argv) > 1 else 'report']()
