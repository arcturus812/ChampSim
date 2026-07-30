#ifndef CXL_REPRO_H
#define CXL_REPRO_H

/*
 * [CXLREPRO] Down-scaled reproduction of the CXL write-allocate problem.
 *
 * Real-hardware finding being reproduced (green_m19, Emerald Rapids + CXL 1.1):
 * write-allocate ownership requests to CXL memory fetch a full line over the
 * link's read direction even though the line is fully overwritten, so on a
 * full-duplex link mixed read/write workloads lose the read-direction
 * bandwidth to fetches. NT stores avoid the fetch and win by ~30%.
 *
 * Runtime knobs (environment variables, read once at startup):
 *   CXL_STORE_POLICY = allocate (default) | nt
 *     allocate: baseline write-allocate; first-level store misses RFO-fetch
 *               the line from far memory (the problem).
 *     nt      : NT-store proxy; first-level store misses to far addresses
 *               bypass allocation and stream directly to the far WQ.
 *   CXL_ALLOC_POLICY = only_far (default) | first_touch | round_robin
 *     page placement policy override for VirtualMemory.
 */

#include <cstdint>

class MEMORY_CONTROLLER;

namespace cxl_repro
{
struct knobs_t {
  bool nt_store = false;          // CXL_STORE_POLICY == "nt"
  int alloc_policy = -1;          // -1: keep branch default; else VirtualMemory policy index
  double livelock_die_ipc = 0.01; // CXL_LIVELOCK_IPC: die threshold of the branch's livelock
                                  // heuristic. Saturated far-memory workloads legitimately run
                                  // below the stock 0.01; the true zero-progress deadlock
                                  // detector stays active regardless.
};

struct stats_t {
  // first-level (L1D) NT bypass path
  uint64_t nt_bypass_lines = 0; // store misses that bypassed allocation to far WQ
  uint64_t nt_bypass_retry = 0; // bypass attempts rejected by a full far WQ (retried)
  // LLC -> far memory request accounting (the directional ledger)
  uint64_t far_rfo_fetches = 0;    // write-allocate fetch reads (casRD analogue)
  uint64_t far_demand_reads = 0;   // LOAD/TRANSLATION demand reads
  uint64_t far_prefetch_reads = 0; // prefetch reads
  uint64_t far_writebacks = 0;     // writeback lines to far WQ
};

knobs_t& knobs();
stats_t& stats();
MEMORY_CONTROLLER*& far_mem();

void reset_stats(); // called when the simulation (non-warmup) phase begins
void print_stats();
} // namespace cxl_repro

#endif
