#!/usr/bin/env python3
"""[CXLELIDE] multi-core solution campaign at the device-derived transaction budget.

What changes relative to run_solution.py, and why:

1. Cores. A single-core trace cannot generate enough memory-level parallelism
   to reach this model's far-link limits -- buses ran 3-61% busy -- so no
   absolute budget ever bound and budgets had to be set per trace, relative to
   each trace's own achieved rate. N cores contending for one shared link (and
   sharing the 2 MiB LLC, so each core misses more) put the model in the
   transaction-limited regime the real machine is in. The same N is used for
   every workload; it is chosen by mc_pilot.sh on a criterion fixed in advance
   -- the smallest N at which the memory-intensive workloads reach the budget --
   not by picking whichever N flatters the mechanism.

2. Budget. One absolute value for every workload, scaled from the device by
   preserving the ratio of its transaction ceiling to its READ-direction
   ceiling, the direction write-allocate traffic loads first:
     device: 4.0e8 tx/s / (18.4 GB/s / 64 B) = 4.0e8 / 2.875e8 = 1.391
     model:  1.391 x (3.2 GB/s / 64 B) = 6.96e7 tx/s -> CXL_TX_PERIOD_PS = 14375
   Preserving the ratio to AGGREGATE byte capacity instead (0.834 x 1.0e8 =
   8.34e7, 11992 ps) looks more principled and is wrong here, which the pilot
   settled: this model's directions are symmetric (3.2:3.2) where the device's
   are not (18.4:12.3), so for any realistically imbalanced mix the aggregate is
   unreachable and a budget placed against it never binds. Measured demand at
   the ceiling -- lbm saturates at four cores and does not rise at eight --
   is 113% of the read-ratio budget but only 94% of the aggregate-ratio one.
   The read-ratio budget also reproduces the device's signature: it pins
   transactions while leaving the read direction near 79% and the write near
   60% busy, against the device's 88% and 83%. The device's measured mixed
   maximum (22.98 GB/s) is deliberately not used anywhere in this derivation:
   it is already depressed by the transaction limit, so it would be circular.

3. No barrier. The budget no longer depends on a measured baseline, so every
   run is independent and the campaign is one flat wave.

Known fidelity gap, stated rather than hidden: the device's directions are
asymmetric (18.4 read : 12.3 write = 1.5:1) and this model's are symmetric
(3.2:3.2). Write-allocate traffic is read-heavy, so in the model the read
direction can bind before the transaction budget on read-heavy workloads, where
on the device transactions bind first. Elide relieves the read direction and the
transaction budget alike, so the measured gain stands either way, but the
binding constraint is not always the same one as on the device.

Usage: run_mc_solution.py run | report
"""
import concurrent.futures as cf
import os
import re
import subprocess
import sys

import run_verify as rv
import run_solution as sol

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, 'out_mc')
TR = sol.TR
POLICIES = sol.POLICIES

# [CXLASYM] The faithful model gives the far link the device's direction split
# (read 3.836 / write 2.564 GB/s, ratio 1.4959) instead of a symmetric 3.2/3.2.
# With that split the two independent ways to scale the device's transaction
# ceiling agree exactly -- against the read direction (4.0e8/2.875e8 x 5.994e7)
# and against aggregate capacity (0.834 x 1.0e8) both give 8.339e7 tx/s. On the
# symmetric link they disagreed by 17%, and that disagreement was the symptom:
# no budget could sit where the device's does. Set MC_ASYM=0 for the old model.
ASYM = os.environ.get('MC_ASYM', '1') != '0'
WR_BUS_RATIO = os.environ.get('MC_WR_BUS_RATIO', '1.4959')
CORES = int(os.environ.get('MC_CORES', '8'))
BUDGET_PS = int(os.environ.get('MC_BUDGET_PS', '11991' if ASYM else '14375'))
WARMUP = int(os.environ.get('MC_WARMUP', '2000000'))
SIM = int(os.environ.get('MC_SIM', '5000000'))
WORKERS = int(os.environ.get('MC_WORKERS', '24'))
TIMEOUT_S = int(os.environ.get('MC_TIMEOUT_S', str(12 * 3600)))
BIN = os.path.join(ROOT, 'bin', ('champsim_cxl_asym%d' if ASYM else 'champsim_cxl_mc%d') % CORES)

# Traces: the same set as the single-core campaign, so the two are comparable.
TRACES = dict(sol.TRACES)

os.makedirs(OUT, exist_ok=True)
rv.OUT = OUT



def already_done(name, outdir):
    p = os.path.join(outdir, name + '.txt')
    if not os.path.exists(p):
        return False
    with open(p) as f:
        return 'ChampSim completed all CPUs' in f.read()

def run_one(name, trace_rel, policy, period_ps):
    if already_done(name, OUT):
        return name, 0
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001')
    if ASYM:
        env['CXL_WR_BUS_RATIO'] = WR_BUS_RATIO
    else:
        env.pop('CXL_WR_BUS_RATIO', None)
    if period_ps:
        env['CXL_TX_PERIOD_PS'] = str(period_ps)
    else:
        env.pop('CXL_TX_PERIOD_PS', None)
    path = os.path.join(TR, trace_rel)
    try:
        with open(os.path.join(OUT, name + '.txt'), 'w') as f:
            p = subprocess.run(
                [BIN, '--warmup-instructions', str(WARMUP),
                 '--simulation-instructions', str(SIM)] + [path] * CORES,
                env=env, stdout=f, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
        return name, p.returncode
    except subprocess.TimeoutExpired:
        return name, -9


def stats(name):
    """Per-core IPCs are reported separately; we use the throughput sum."""
    d = rv.parse(name)
    text = open(os.path.join(OUT, name + '.txt')).read()
    ipcs = [float(x) for x in
            re.findall(r'CPU \d+ cumulative IPC: ([\d.]+)', text)]
    d['ipcs'] = ipcs
    d['ipc_sum'] = sum(ipcs)                      # system throughput
    d['ipc_mean'] = sum(ipcs) / len(ipcs) if ipcs else 0.0
    m = re.search(r'FAR_CHANNEL_0 TX_GRANTS:\s+(\d+)', text)
    d['tx_grants'] = int(m.group(1)) if m else 0
    m = re.search(r'CXLELIDE elided_grants:\s+(\d+)', text)
    d['elided'] = int(m.group(1)) if m else 0
    if d.get('roi_sec') and d.get('far'):
        lines = d['far'].get('rd_lines', 0) + d['far'].get('wr_lines', 0)
        d['line_rate'] = lines / d['roi_sec']
        d['tx_rate'] = d['tx_grants'] / d['roi_sec']
        useful_wr = d['nt_bypass'] if d['nt_bypass'] > 0 else d['wb']
        d['useful_lines'] = d['demand_rd'] + useful_wr
        d['txpl'] = lines / d['useful_lines'] if d['useful_lines'] else 0.0
    return d


def do_run():
    if not os.path.exists(BIN):
        sys.exit('missing %s -- build it with cxl_repro_%s%d_config.json'
             % (BIN, 'asym' if ASYM else 'mc', CORES))
    jobs = [('%s_%s_%s' % (k, p, mode), t, p, ps)
            for k, t in TRACES.items() for p in POLICIES
            for mode, ps in (('off', 0), ('bud', BUDGET_PS))]
    print('[mc] %s link, %d cores, budget %d ps (%.3e tx/s), %d warmup + %d sim per core, %d runs'
          % ('asymmetric' if ASYM else 'symmetric', CORES, BUDGET_PS, 1e12 / BUDGET_PS,
             WARMUP, SIM, len(jobs)), flush=True)
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(run_one, *j): j[0] for j in jobs}
        for fu in cf.as_completed(futs):
            try:
                print('[mc] %-26s rc=%d' % fu.result(), flush=True)
            except Exception as e:
                print('[mc] %-26s EXC %s' % (futs[fu], e), flush=True)
    print('MC_RUN_DONE', flush=True)


def do_report():
    target = 1e12 / BUDGET_PS
    print('%d cores, budget %.3e tx/s\n' % (CORES, target))
    print('%-9s %8s %8s %8s %7s | %8s %8s %8s %7s %6s' %
          ('trace', 'IPCa off', 'IPCe off', 'e/a', 'demand',
           'IPCa bud', 'IPCn bud', 'IPCe bud', 'e/a', 'binds'))
    gains = []
    for k in TRACES:
        try:
            S = {(p, m): stats('%s_%s_%s' % (k, p, m))
                 for p in POLICIES for m in ('off', 'bud')}
        except FileNotFoundError:
            continue
        if not all(v['completed'] for v in S.values()):
            print('%-9s incomplete' % k)
            continue
        f = lambda p, m, key='ipc_sum': S[(p, m)].get(key, 0.0)
        r = lambda a, b: a / b if b else float('nan')
        demand = f('allocate', 'off', 'line_rate')
        binds = demand > target
        eb = r(f('elide', 'bud'), f('allocate', 'bud'))
        if binds:
            gains.append(eb)
        print('%-9s %8.4f %8.4f %7.3f %8.2e | %8.4f %8.4f %8.4f %7.3f %6s' %
              (k, f('allocate', 'off'), f('elide', 'off'),
               r(f('elide', 'off'), f('allocate', 'off')), demand,
               f('allocate', 'bud'), f('nt', 'bud'), f('elide', 'bud'), eb,
               'yes' if binds else 'no'))
    if gains:
        import math
        geo = math.exp(sum(math.log(g) for g in gains if g > 0) / len(gains))
        print('\n%d of %d traces reach the budget; elide/allocate geomean %.3f (%+.1f%%)'
              % (len(gains), len(TRACES), geo, 100 * (geo - 1)))


if __name__ == '__main__':
    {'run': do_run, 'report': do_report}[sys.argv[1] if len(sys.argv) > 1 else 'report']()
