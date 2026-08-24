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
 *   CXL_TX_PERIOD_PS = 0 (default, disabled) | <picoseconds>
 *     Shared transaction-rate budget on the far link: at most one serviced line,
 *     read or write, per this period. Models the measured downstream completion-rate
 *     ceiling, which per-direction byte bandwidth does not capture.
 *   CXL_TX_RATE_MTPS = <mega-transactions per second>
 *     Convenience form; period_ps = 1e6 / rate. CXL_TX_PERIOD_PS wins if both are set.
 *
 * Calibration note. The device measures ~4.0e8 completions/s against an 18.4 GB/s read
 * and 12.3 GB/s write ceiling. This model's far link is 3.2 GB/s per direction, a
 * bandwidth scale of ~5.75x down, so the corresponding budget is ~7.0e7 tx/s, i.e.
 * CXL_TX_PERIOD_PS=14286 (CXL_TX_RATE_MTPS=70).
 */

#include <cstdint>
#include <cstddef>
#include <deque>

class MEMORY_CONTROLLER;

namespace cxl_repro
{
struct knobs_t {
  bool nt_store = false;          // CXL_STORE_POLICY == "nt"
  // [CXLMASK] What to do when an elided line reaches eviction only partly written.  The
  // three settings bracket the mechanism: `bound` is the optimistic figure that assumes
  // correctness is free, `fetch` is the pessimistic one that assumes no protocol help is
  // available, and `safe` is the proposal.  The gap between them is the cost of being
  // correct, which has never been measured.
  enum class partial_policy_t {
    bound, // CXL_STORE_POLICY == "elide": write the line back as if it were whole
    safe,  // == "elide_safe": MemWrPtl -- same transaction, byte enables on the wire
    fetch  // == "elide_fetch": read the line to merge, then write it -- two transactions
  };

  partial_policy_t partial_policy = partial_policy_t::bound;
  bool elide_store = false;       // [CXLELIDE] CXL_STORE_POLICY == "elide": local ownership
                                  // completion -- a store's ownership fetch to far memory is
                                  // granted at the LLC without a far transaction; the write
                                  // (writeback) is the only thing that crosses the link
  int alloc_policy = -1;          // -1: keep branch default; else VirtualMemory policy index
  double livelock_die_ipc = 0.01; // CXL_LIVELOCK_IPC: die threshold of the branch's livelock
                                  // heuristic. Saturated far-memory workloads legitimately run
                                  // below the stock 0.01; the true zero-progress deadlock
                                  // detector stays active regardless.
  long tx_period_ps = 0;          // [CXLTX] 0 disables the shared transaction-rate budget
  double wr_bus_ratio = 1.0;      // [CXLASYM] far write direction slowed by this factor;
                                  // 1.0 = stock symmetric duplex link
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
  uint64_t elided_grants = 0;      // [CXLELIDE] far RFOs completed locally (no far transaction)
};

/*
 * [CXLMASK] Per-line byte coverage for elided ownership grants.
 *
 * Granting ownership without data leaves the line partially valid until the stores that
 * motivated the grant have covered it.  Whether that is safe is decided by which bytes are
 * written before the line leaves the cache -- not by any single access.  A 64 B store does
 * not settle it (SPEC17 rate emits none: 519.lbm_r covers lines with 8 B stores 70% of the
 * time), and neither does a rep stosb that writes 128 B one byte at a time.  Coverage is
 * the union of byte ranges over the line's residency, which is what this accumulates.
 *
 * The table is deliberately not attached to a cache block.  The grant happens at the LLC,
 * but the line then fills into L2 and L1 and the covering stores hit there; an LLC-side
 * mask would observe almost none of them.  So the mask lives in a line-indexed side table,
 * fixed-size and direct-mapped: no allocation on the hot path, and deterministic.
 *
 * Displacement is counted, never silent.  A conflict that drops a live episode is reported
 * as evicted_by_conflict, because a quietly truncated table reads as "everything was
 * covered" -- the failure mode this whole measurement exists to avoid.
 */
struct mask_stats_t {
  uint64_t episodes_opened = 0;
  uint64_t episodes_closed = 0;         // reached a far writeback while still tracked
  uint64_t closed_full = 0;             // all 64 bytes written: eliding the fetch was safe
  uint64_t closed_partial = 0;          // some byte never written: needs MemWrPtl or a fetch
  uint64_t closed_read_uncovered = 0;   // a load read a byte the episode never wrote
  uint64_t closed_irregular = 0;        // an ACCESS_SIZE_IRREGULAR store touched the line
  uint64_t evicted_by_conflict = 0;     // displaced from the table before its writeback
  uint64_t covered_bytes_sum = 0;       // over closed episodes, for the mean coverage
  // A mean hides the shape, and the shape is the question: a line covered 63 of 64 bytes and
  // one covered 4 both count as partial, but they say opposite things about whether a
  // byte-granular write primitive is worth its cost.  Buckets at close:
  //   0 | 1-8 | 9-16 | 17-24 | 25-32 | 33-40 | 41-48 | 49-56 | 57-63 | 64
  // The two extremes get their own bucket because they are the two answers that end the
  // argument; the middle is where it has to be made.
  uint64_t covered_hist[10] = {};
  // Whether the accumulator is doing real work.  If one store covers the line, a mask is
  // unnecessary and an instruction-level "is this a full-line store" test would have done;
  // 17.4.3 found no aligned 64 B store in SPEC17 rate, so this is expected near zero, and
  // an unexpectedly large value would mean the size plumbing is wrong rather than that the
  // workload changed.
  uint64_t full_on_first_store = 0;      // the first store on the line covered all 64 bytes
  uint64_t partial_to_full_by_store = 0; // a later store completed what earlier ones began
  uint64_t stores_tracked = 0;          // stores landing on a tracked line
  uint64_t stores_unsized = 0;          // ... of those, ones the trace gave no size for
  uint64_t straddling_stores = 0;       // stores crossing a line boundary (both lines get it)
  uint64_t live_max = 0;                // high-water mark of concurrently partial lines
  uint64_t live_hist[8] = {};           // log2 bucket of the live count when an episode opens
  uint64_t far_store_size_hist[8] = {}; // log2 bucket of far store sizes (1,2,4,...,>=128)
  uint64_t far_store_aligned[8] = {};   // ... of those, naturally aligned to their own size
  uint64_t partial_writebacks = 0;      // closed partial under `safe`: sent with byte enables
  uint64_t merge_fetches = 0;           // closed partial under `fetch`: extra read to merge
  // Ledger and bias counters (blueprint critic F4-F7).  The ledger identity
  //   episodes_opened == episodes_closed + evicted_by_conflict + live_now
  // is the one gate that catches episodes vanishing silently; every unexplained residual
  // so far has been a bias in the direction that flatters the proposal.
  uint64_t regrant_on_open = 0;          // a second grant landed on an already-open episode
  uint64_t untracked_far_writebacks = 0; // far WBs the cost model exempted: no episode was
                                         // tracked for them (ordinary full lines, but also
                                         // displaced episodes and warmup survivors)
  uint64_t fetch_on_open_episode = 0;    // a non-RFO far read fetched a line mid-episode:
                                         // the (E2) oracle assumption is violated this often
  // 4 B word projection, derived from the byte mask at close: how many of the 16 words
  // were fully covered, and whether any store failed word alignment.  This is what decides
  // the hardware granularity (blueprint 4) -- store-level alignment stats cannot.
  uint64_t covered_words_hist[17] = {};
  uint64_t subword_episodes = 0;         // episodes that saw a store with off%4 or len%4 != 0
  // Merge-serialization escape hatches, both counted so the timing model's honesty is
  // checkable.  The far channel coalesces same-block requests (check_collision RQ-merge)
  // and forwards reads from queued writes; either breaks the one-request-one-response
  // assumption serialization rests on.  When a same-block request is already in flight,
  // the merge falls back to unserialized accounting instead of racing the channel.
  // ★ How many partial lines are resident in the LLC at once -- the quantity a per-LLC
  // side table would actually have to hold.  live_max is NOT that: an elide grant fills
  // the LLC line clean (RFO, not WRITE), a clean victim never reaches the far writeback,
  // so mask_close never fires and the episode stays counted until the dirty copy above
  // finally writes back.  Measured directly here instead of inferred (critic H3).
  uint64_t llc_resident_now = 0;
  uint64_t llc_resident_max = 0;
  uint64_t llc_clean_drops = 0;      // granted lines whose LLC copy left without a writeback
  // Subword fallback's real cost is the intersection with partial closure, not the sticky
  // flag alone: an episode that saw a misaligned store but still closed full costs nothing.
  uint64_t closed_partial_subword = 0;
  // ★ Granularity projection, G = 4 / 8 / 16 / 32 B (index 0..3).  Two hardware models,
  // because the honest answer is a bracket, not a point:
  //
  //   Model A (accumulating) -- byte-granular staging ahead of the cache (the store buffer
  //     already coalesces) fills a chunk before its bit is set.  A chunk left *partly*
  //     written at close cannot be represented, so that line must be fetched.  This is the
  //     optimistic bound and it is computable from the byte mask.
  //   Model S (strict) -- a chunk bit is set only by one store that covers the whole chunk,
  //     which is Jouppi's own recommendation (fetch-on-write for narrower writes).  Any
  //     store failing off%G or len%G forces the fallback.  Pessimistic bound.
  //
  // The simulator does not model store-buffer coalescing, so the truth is between them.
  uint64_t gran_representable[4] = {}; // Model A: mask is G-clean (every chunk all-or-none)
  uint64_t gran_forced_fetch[4] = {};  // Model A: some chunk partly written -> must fetch
  uint64_t gran_strict_fallback[4] = {}; // Model S: saw a store not aligned/sized to G
  uint64_t merge_unserialized = 0;   // merge fetches issued fire-and-forget due to a conflict
  uint64_t merge_orphan_dropped = 0; // far responses claimed by neither the map nor any MSHR
  // Finite-table overflow.  Displacing a live episode is the hardware event a small
  // partial-line table has to survive: the mask is gone, so the line must be fetched
  // whole before anyone can read it.  Counting the displacement without charging that
  // fetch makes a smaller table look faster, which is the wrong sign for the sensitivity
  // study the paper needs -- so the fetch is issued for real and counted here.
  uint64_t admit_rejected = 0;        // grants declined because the table had no room
  uint64_t overflow_materializes = 0; // displaced episodes that were charged a far read
  uint64_t materialize_dropped = 0;   // ... that could not be queued (backlog cap hit)
  // ★ [CXLGATE] Hierarchical tracking units (1 B in L1D, 4 B below).  A line whose byte
  // mask leaves a 4 B word partly written cannot be represented once it crosses out of
  // L1D, so that line owes the fetch that makes it whole.  The question these answer is
  // the one the close-time granularity projection cannot: how full is the mask *at the
  // crossing*, not at the far writeback.  Evaluation is counted on every crossing;
  // conversion is self-terminating because a materialised mask is full and stays clean.
  uint64_t crossings_l1 = 0;           // dirty granted victim left L1D
  uint64_t crossings_l1_unclean = 0;   // ... with a partly-written 4 B word
  uint64_t crossings_l2 = 0;           // dirty granted victim left L2 (4 B -> 4 B)
  uint64_t crossings_l2_unclean = 0;   // must stay 0: crossing 4 B -> 4 B loses nothing
  uint64_t crossings_untracked_l1 = 0; // dirty far victim with no open episode (bounds the
  uint64_t crossings_untracked_l2 = 0; //   population the `granted` predicate excludes)
  uint64_t crossings_clean_skipped_l1 = 0; // granted victim that was not dirty: an RFO fill
  uint64_t crossings_clean_skipped_l2 = 0; //   installs clean, so it carries no dirty bytes
  uint64_t gate_materializes = 0;      // conversions charged a real far read (enforce mode)
  uint64_t gate_materialize_dropped = 0;
  uint64_t closed_gate_materialized = 0; // closed episodes that were converted at a crossing
  uint64_t episodes_no_crossing = 0;   // closed without ever leaving L1D (censoring account)
  uint64_t crossing_hist[8] = {};      // crossings per episode, taken at close
};

// What an episode looks like at the moment its line is about to leave the cache.  Read
// before the writeback is issued, because the policy may have to put another request on
// the link first.
struct mask_verdict_t {
  bool tracked = false;       // an episode is open for this line
  bool complete = false;      // all 64 bytes written, and none of them irregularly
  bool merge_issued = false;  // a merge fetch for this eviction is already in flight
  bool merge_arrived = false; // that fetch's data is back; the writeback may proceed
};

mask_stats_t& mask_stats();

// Coverage tracking, driven from three points in the cache: the elide grant opens an
// episode, accesses accumulate into it, and the far writeback closes it.
bool mask_tracking_enabled();
// Returns false when admission control is on and tracking this line would displace a live
// episode.  The caller must then NOT elide: the line takes the ordinary write-allocate path.
// Declining up front is strictly better than eliding and repairing later -- the repair fetch
// happens at eviction, serialised behind a held victim way, where the ordinary fetch happens
// at fill and pipelines (measured: 0.20x vs 1.00x on 519.lbm_r without a budget).
bool mask_open(uint64_t line_addr);
void mask_store(uint64_t byte_addr, unsigned char size);
void mask_load(uint64_t byte_addr, unsigned char size);
mask_verdict_t mask_peek(uint64_t line_addr);
void mask_mark_merge_issued(uint64_t line_addr);
bool mask_merge_consume(uint64_t line_addr); // claim a merge fetch's response (no MSHR entry exists for it)
void mask_merge_done(uint64_t line_addr);    // the waited-on writeback was sent; forget the merge

// Lines displaced from the tracking table, awaiting the far read that makes them whole.
// The cache drains this; cxl_repro cannot issue requests itself.
std::deque<uint64_t>& materialize_pending();
std::size_t mask_table_entries(); // configured size, for the sensitivity sweep
bool mask_episode_open(uint64_t line_addr);  // is a granted episode currently tracked for this line
// [CXLGATE] A tracked line is crossing out of a level into one whose tracking unit is
// coarser.  level 1 = L1D -> L2 (byte -> 4 B, the gate that can fail); level 2 = L2 -> LLC
// (4 B -> 4 B, audited only: it must never fail).  Call at the fill commit point.
void mask_crossing(uint64_t line_addr, unsigned level, bool dirty);
int gate_mode(); // 0 = off, 1 = observe (no behaviour change), 2 = enforce (charge the fetch)
void mask_llc_insert(uint64_t line_addr);    // a granted line took up residence in the LLC
void mask_llc_evict(uint64_t line_addr, bool had_writeback); // ... and lost it
void mask_close(uint64_t line_addr, bool sent_partial, bool merged_by_fetch);
void mask_reset();

knobs_t& knobs();
stats_t& stats();
MEMORY_CONTROLLER*& far_mem();

void reset_stats(); // called when the simulation (non-warmup) phase begins
void print_stats();
} // namespace cxl_repro

#endif
