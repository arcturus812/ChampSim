#!/usr/bin/env python3
"""[CXLELIDE] verify the solution campaign, then report per-trace IPC.

Two jobs, in this order, because the second is only worth reading if the first
passes: check that the policy did on real traces exactly what it is defined to
do, then report what it bought.

Verification, per trace (the same invariants run_verify_elide.py checks on
synthetic traces, re-checked here where the access pattern is not ours):
  V1 every run completed
  V2 allocate is untouched by the knob (no grants recorded)
  V3 elide converts every far ownership fetch into a local grant
  V4 elide's read direction = allocate's, less exactly those fetches
  V5 elide's write direction is allocate's, unchanged
  V6 the [CXLTX] slot invariant holds (grants == serviced lines)
  V7 binding runs actually reach their budget
  V8 nt is the unchanged control (no fetches, no grants)

Usage: analyze_solution.py [verify | ipc | all]
"""
import json
import os
import re
import sys

import run_verify as rv
import run_solution as sol

rv.OUT = sol.OUT
POLICIES = sol.POLICIES
TOL = 0.05


def enrich(d):
    """run_solution.stats leaves the transaction rate out; V7 needs it."""
    if d.get('roi_sec'):
        d['tx_rate'] = d.get('tx_grants', 0) / d['roi_sec']
    else:
        d['tx_rate'] = 0.0
    return d


def load():
    """{(trace, policy, mode): stats}; missing runs are absent, not fatal."""
    R = {}
    for k in sol.TRACES:
        for p in POLICIES:
            for m in ('off', 'bind'):
                name = '%s_%s_%s' % (k, p, m)
                if os.path.exists(os.path.join(sol.OUT, name + '.txt')):
                    try:
                        R[(k, p, m)] = enrich(sol.stats(name))
                    except Exception as e:
                        print('  ! parse failed: %s (%s)' % (name, e))
    return R


def close(a, b, tol=TOL):
    return abs(a - b) <= tol * max(abs(b), 1.0)


def verify(R, budgets):
    checks = {n: [0, 0, []] for n in
              ('V1 complete', 'V2 allocate inert', 'V3 fetches become grants',
               'V4 read direction', 'V5 write direction', 'V6 slot invariant',
               'V7 budget binds', 'V8 nt control')}

    def note(key, ok, trace, msg=''):
        checks[key][1] += 1
        if ok:
            checks[key][0] += 1
        elif msg:
            checks[key][2].append('%s: %s' % (trace, msg))

    for k in sol.TRACES:
        have = {(p, m): R.get((k, p, m)) for p in POLICIES for m in ('off', 'bind')}
        if any(v is None for v in have.values()):
            note('V1 complete', False, k, 'missing %d of 6 runs' %
                 sum(v is None for v in have.values()))
            continue
        note('V1 complete', all(v['completed'] for v in have.values()), k,
             'did not finish: ' + ','.join('%s_%s' % key for key, v in have.items()
                                           if not v['completed']))
        if not all(v['completed'] for v in have.values()):
            continue

        for m in ('off', 'bind'):
            a, n, e = have[('allocate', m)], have[('nt', m)], have[('elide', m)]
            note('V2 allocate inert', a['elided'] == 0, k, 'grants %d' % a['elided'])
            note('V8 nt control', n['elided'] == 0 and n['rfo_fetch'] == 0, k,
                 'nt grants %d fetches %d' % (n['elided'], n['rfo_fetch']))
            note('V3 fetches become grants',
                 e['rfo_fetch'] == 0 and (a['rfo_fetch'] == 0 or
                                          close(e['elided'], a['rfo_fetch'], 0.10)),
                 k, 'elide fetches %d, grants %d vs alloc fetches %d'
                 % (e['rfo_fetch'], e['elided'], a['rfo_fetch']))
            note('V4 read direction',
                 close(e['far']['rd_lines'], a['far']['rd_lines'] - a['rfo_fetch']),
                 k, 'rd %d, expected %d' %
                 (e['far']['rd_lines'], a['far']['rd_lines'] - a['rfo_fetch']))
            note('V5 write direction',
                 close(e['far']['wr_lines'], a['far']['wr_lines']),
                 k, 'wr %d vs %d' % (e['far']['wr_lines'], a['far']['wr_lines']))

        for p in POLICIES:
            b = have[(p, 'bind')]
            lines = b['far']['rd_lines'] + b['far']['wr_lines']
            note('V6 slot invariant', abs(b['tx_grants'] - lines) <= 8, k,
                 '%s: tx %d vs lines %d' % (p, b['tx_grants'], lines))
            if k in budgets:
                target = 1e12 / budgets[k]
                note('V7 budget binds', close(b['tx_rate'], target, 0.05), k,
                     '%s: %.3e vs budget %.3e' % (p, b['tx_rate'], target))

    print('== Verification')
    allok = True
    for name, (ok, tot, fails) in checks.items():
        mark = 'PASS' if ok == tot and tot else ('FAIL' if tot else 'n/a ')
        allok &= (ok == tot and tot > 0)
        print('%s %-26s %3d/%-3d' % (mark, name, ok, tot))
        for f in fails[:3]:
            print('       %s' % f)
        if len(fails) > 3:
            print('       ... and %d more' % (len(fails) - 3))
    print('\n%s\n' % ('ALL INVARIANTS HOLD' if allok else 'SOME INVARIANTS FAILED'))
    return allok


def ipc_table(R, budgets):
    print('== IPC per trace: does local ownership completion pay?')
    print('%-9s|%18s|%26s|%15s' %
          ('', ' budget off (IPC)', '  budget binding (IPC)', ' tx per line'))
    print('%-9s %7s %7s %7s %7s %7s %7s %7s %7s %6s %6s' %
          ('trace', 'alloc', 'elide', 'gain', 'alloc', 'nt', 'elide', 'e/a', 'e/nt',
           'txpl a', 'txpl e'))
    rows = []
    for k in sol.TRACES:
        need = [(p, m) for p in POLICIES for m in ('off', 'bind')]
        if any((k, p, m) not in R or not R[(k, p, m)]['completed'] for p, m in need):
            print('%-9s incomplete' % k)
            continue
        g = lambda p, m, f='ipc': R[(k, p, m)].get(f, 0.0)
        ao, eo = g('allocate', 'off'), g('elide', 'off')
        ab, nb, eb = g('allocate', 'bind'), g('nt', 'bind'), g('elide', 'bind')
        r = lambda x, y: x / y if y else float('nan')
        rows.append((k, r(eo, ao), r(eb, ab), r(eb, nb)))
        print('%-9s %7.4f %7.4f %+6.1f%% %7.4f %7.4f %7.4f %7.3f %7.3f %6.3f %6.3f' %
              (k, ao, eo, 100 * (r(eo, ao) - 1), ab, nb, eb, r(eb, ab), r(eb, nb),
               g('allocate', 'bind', 'txpl'), g('elide', 'bind', 'txpl')))
    if not rows:
        return
    geo = lambda xs: (lambda v: v)(__import__('math').exp(
        sum(__import__('math').log(x) for x in xs if x > 0) / len([x for x in xs if x > 0])))
    print('\n%-9s %39s %7.3f %7.3f' %
          ('geomean', 'elide/alloc off %.3f, binding' % geo([x[1] for x in rows]),
           geo([x[2] for x in rows]), geo([x[3] for x in rows])))
    won = sum(1 for x in rows if x[2] > 1.0)
    print('elide beats allocate under a binding budget on %d of %d traces; '
          'beats nt on %d' % (won, len(rows), sum(1 for x in rows if x[3] > 1.0)))


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    budgets = json.load(open(sol.PLAN)) if os.path.exists(sol.PLAN) else {}
    R = load()
    if mode in ('verify', 'all'):
        verify(R, budgets)
    if mode in ('ipc', 'all'):
        ipc_table(R, budgets)
