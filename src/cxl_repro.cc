#include "cxl_repro.h"

#include <algorithm>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <unordered_map>
#include <vector>

#include <fmt/core.h>

#include "dram_controller.h"
#include "trace_instruction.h" // [CXLMASK] ACCESS_SIZE_* encodings

namespace cxl_repro
{
namespace
{
knobs_t parse_knobs()
{
  knobs_t k{};
  if (const char* env = std::getenv("CXL_STORE_POLICY"); env != nullptr) {
    if (std::strcmp(env, "nt") == 0) {
      k.nt_store = true;
    } else if (std::strcmp(env, "elide") == 0) {
      k.elide_store = true; // [CXLELIDE]
    } else if (std::strcmp(env, "elide_safe") == 0) {
      k.elide_store = true; // [CXLMASK] proposal A with the partial-line safety net
      k.partial_policy = knobs_t::partial_policy_t::safe;
    } else if (std::strcmp(env, "elide_fetch") == 0) {
      k.elide_store = true; // [CXLMASK] proposal A with no protocol help for partial lines
      k.partial_policy = knobs_t::partial_policy_t::fetch;
    } else if (std::strcmp(env, "allocate") != 0) {
      fmt::print("[CXLREPRO] WARNING: unknown CXL_STORE_POLICY '{}', using 'allocate'\n", env);
    }
  }
  if (const char* env = std::getenv("CXL_LIVELOCK_IPC"); env != nullptr) {
    k.livelock_die_ipc = std::atof(env);
  }
  // [CXLTX] shared transaction-rate budget on the far link
  if (const char* env = std::getenv("CXL_TX_RATE_MTPS"); env != nullptr) {
    const double rate = std::atof(env);
    if (rate > 0.0) {
      k.tx_period_ps = static_cast<long>(1.0e6 / rate + 0.5);
    } else {
      fmt::print("[CXLTX] WARNING: CXL_TX_RATE_MTPS '{}' is not positive, budget stays disabled\n", env);
    }
  }
  if (const char* env = std::getenv("CXL_TX_PERIOD_PS"); env != nullptr) {
    k.tx_period_ps = std::atol(env);
    if (k.tx_period_ps < 0) {
      fmt::print("[CXLTX] WARNING: CXL_TX_PERIOD_PS '{}' is negative, budget stays disabled\n", env);
      k.tx_period_ps = 0;
    }
  }
  // [CXLASYM] far-link direction asymmetry, e.g. 1.4959 for the device's 18.4:12.3
  if (const char* env = std::getenv("CXL_WR_BUS_RATIO"); env != nullptr) {
    const double ratio = std::atof(env);
    if (ratio >= 1.0) {
      k.wr_bus_ratio = ratio;
    } else {
      fmt::print("[CXLASYM] WARNING: CXL_WR_BUS_RATIO '{}' is below 1.0, link stays symmetric\n", env);
    }
  }
  if (const char* env = std::getenv("CXL_ALLOC_POLICY"); env != nullptr) {
    if (std::strcmp(env, "first_touch") == 0) {
      k.alloc_policy = 0;
    } else if (std::strcmp(env, "only_far") == 0) {
      k.alloc_policy = 1;
    } else if (std::strcmp(env, "round_robin") == 0) {
      k.alloc_policy = 2;
    } else {
      fmt::print("[CXLREPRO] WARNING: unknown CXL_ALLOC_POLICY '{}', keeping default\n", env);
    }
  }
  return k;
}
} // namespace

knobs_t& knobs()
{
  static knobs_t k = parse_knobs();
  return k;
}

stats_t& stats()
{
  static stats_t s{};
  return s;
}

MEMORY_CONTROLLER*& far_mem()
{
  static MEMORY_CONTROLLER* mc = nullptr;
  return mc;
}

// --------------------------------------------------------------------------
// [CXLMASK] byte-coverage side table
// --------------------------------------------------------------------------
namespace
{
constexpr uint64_t LINE_BYTES = 64;
constexpr uint64_t LINE_MASK_FULL = ~uint64_t{0}; // 64 bytes, one bit each

// The model's LLC is 32,768 lines; 2^17 entries keeps conflicts rare while staying a flat
// ~3 MB array.  Sizing it is not the paper's claim -- overflow degrades to a fetch, which
// is a performance loss and not a correctness one -- but it has to be counted to say so.
// CXL_MASK_ENTRIES overrides it (rounded down to a power of two) for the sensitivity sweep.
std::size_t table_entries()
{
  static std::size_t n = [] {
    std::size_t want = 1U << 17;
    if (const char* env = std::getenv("CXL_MASK_ENTRIES"); env != nullptr) {
      const long v = std::atol(env);
      if (v >= 1) {
        std::size_t p = 1;
        while ((p << 1) <= static_cast<std::size_t>(v)) {
          p <<= 1;
        }
        want = p;
      } else {
        fmt::print("[CXLMASK] WARNING: CXL_MASK_ENTRIES '{}' is not positive, keeping default\n", env);
      }
    }
    return want;
  }();
  return n;
}

// Admission control (CXL_MASK_ADMIT=1): refuse the grant instead of displacing a live
// episode.  Off by default so the earlier overflow-and-materialise curve stays reproducible.
bool admit_mode()
{
  static bool on = [] {
    const char* env = std::getenv("CXL_MASK_ADMIT");
    return env != nullptr && std::atol(env) != 0;
  }();
  return on;
}
} // namespace

// [CXLGATE] Hierarchical tracking units (CXL_GATE_L1).  0 = off, and then nothing about the
// simulation changes: the baseline output stays byte-identical, which is what makes the
// earlier campaigns comparable.  1 = observe: count the crossings and their cleanliness but
// leave behaviour alone, so the frequency can be read without disturbing the IPC it would
// disturb.  2 = enforce: an unrepresentable crossing queues the real far read, so the cost
// lands in the transaction budget rather than in a projection.
int gate_mode()
{
  static int mode = [] {
    const char* env = std::getenv("CXL_GATE_L1");
    if (env == nullptr) {
      return 0;
    }
    const long v = std::atol(env);
    return (v < 0) ? 0 : ((v > 2) ? 2 : static_cast<int>(v));
  }();
  return mode;
}

namespace
{

// A displaced episode owes a far read before anything may read the line, and that read has
// to be a real request so it spends the transaction budget (the whole point of the study).
// cxl_repro cannot issue requests, so displaced lines queue here for the LLC to drain.
// The cap is a backstop: at tiny table sizes the queue is the overflow rate itself, and an
// unbounded deque would trade a measurement for an OOM.
constexpr std::size_t MATERIALIZE_CAP = 1U << 16;

struct mask_entry {
  uint64_t line = 0;
  uint64_t mask = 0;
  bool valid = false;
  bool granted = false;       // an elide grant has been seen for this line
  bool read_uncovered = false;
  bool irregular = false;      // a masked or scattered store landed here: never judge it full
  bool subword = false;        // a store failed word alignment (off%4 or len%4 != 0)
  bool llc_resident = false;   // this granted line currently occupies an LLC way
  uint8_t strict_fail = 0;     // bit g set: a store failed alignment/size for GRANS[g]
  uint32_t stores = 0;         // stores that landed on this line, in arrival order
  uint32_t full_at_store = 0;  // ordinal of the store that first completed it; 0 = never
  // [CXLGATE] What the *next level down* holds, as opposed to what the byte-exact mask
  // holds.  Asking the global mask whether a crossing is representable answers the wrong
  // question -- it includes bytes that are still only in L1D -- so the projection that
  // actually descends is snapshotted here at the crossing (critic B1).
  uint16_t l2_words = 0;       // 4 B words that have descended out of L1D
  uint8_t crossings = 0;       // crossings this episode has made, saturating
  bool gate_materialized = false; // a crossing forced the fetch that made this line whole
};


std::vector<mask_entry>& mask_table()
{
  static std::vector<mask_entry> t(table_entries());
  return t;
}

uint64_t& mask_live()
{
  static uint64_t live = 0;
  return live;
}

std::size_t mask_index(uint64_t line_addr) { return (line_addr / LINE_BYTES) & (table_entries() - 1); }

// [CXLMASK] Merge-fetch serialization state, deliberately NOT in the mask table.  The
// table is direct-mapped and an episode can be displaced while its merge read is still
// on the link; if the in-flight state lived in the displaced entry, the response would
// find no consumer and fall through to the MSHR search, which asserts (this happened:
// 519.lbm_r-413B elide_fetch, 2026-08-14).  A separate map keyed by line survives
// displacement.  States: ISSUED (false) -> ARRIVED (true); erased when the writeback
// that waited on it is finally sent.
std::unordered_map<uint64_t, bool>& merge_inflight()
{
  static std::unordered_map<uint64_t, bool> m;
  return m;
}

mask_entry* mask_find(uint64_t line_addr)
{
  mask_entry& e = mask_table()[mask_index(line_addr)];
  return (e.valid && e.line == line_addr) ? &e : nullptr;
}

// An entry has to exist before the grant does.  The store that misses is the reason the
// grant happens, and the stores that follow it are issued while the fill is still in
// flight -- they reach the first-level tag check before the LLC has granted anything.
// Those are exactly the stores that cover the line, so a table keyed only on granted
// lines misses the ones that matter.  Entries are therefore created on first touch and
// only counted as episodes once a grant marks them.
mask_entry* mask_obtain(uint64_t line_addr, bool& displaced, uint64_t& displaced_line)
{
  mask_entry& e = mask_table()[mask_index(line_addr)];
  displaced = false;
  displaced_line = 0;
  if (e.valid && e.line == line_addr) {
    return &e;
  }
  if (e.valid && e.granted) {
    if (admit_mode()) {
      return nullptr; // the slot is taken by a live episode; caller must not displace it
    }
    displaced = true; // a live episode is being lost; the caller reports it
    displaced_line = e.line;
  }
  e.line = line_addr;
  e.mask = 0;
  e.valid = true;
  e.granted = false;
  e.read_uncovered = false;
  e.irregular = false;
  e.subword = false;
  e.llc_resident = false;
  e.strict_fail = 0;
  e.stores = 0;
  e.full_at_store = 0;
  e.l2_words = 0;
  e.crossings = 0;
  e.gate_materialized = false;
  return &e;
}

// Bits [off, off+len) of a 64-byte line.  len == 64 is the whole line; shifting by 64 is
// undefined, so that case is named rather than computed.
uint64_t range_mask(uint64_t off, uint64_t len)
{
  if (len == 0) {
    return 0;
  }
  if (off == 0 && len >= LINE_BYTES) {
    return LINE_MASK_FULL;
  }
  const uint64_t end = std::min(off + len, LINE_BYTES);
  return ((uint64_t{1} << (end - off)) - 1) << off;
}

// Covered bytes at close, bucketed.  0 and 64 are named on their own because they are the
// two conclusive answers; everything between them is the case that has to be argued.
std::size_t coverage_bucket(uint64_t covered)
{
  if (covered == 0) {
    return 0;
  }
  if (covered >= LINE_BYTES) {
    return 9;
  }
  return 1 + static_cast<std::size_t>((covered - 1) / 8); // 1-8 -> 1, ..., 57-63 -> 8
}

// Granularities priced against the 4 B word: 8 b, 4 b, 2 b of mask per line respectively.
constexpr uint64_t GRANS[4] = {4, 8, 16, 32};

// Model A: is every G-chunk of the mask either fully written or fully unwritten?  A chunk
// caught in between has no representation at this granularity, so the line must be fetched.
bool g_clean(uint64_t mask, uint64_t g)
{
  const uint64_t chunk = (g >= LINE_BYTES) ? LINE_MASK_FULL : ((uint64_t{1} << g) - 1);
  for (uint64_t off = 0; off < LINE_BYTES; off += g) {
    const uint64_t bits = (mask >> off) & chunk;
    if (bits != 0 && bits != chunk) {
      return false;
    }
  }
  return true;
}

// [CXLGATE] The 4 B words a byte mask has fully written -- what a 16-bit tracker below L1D
// can actually hold.  Partly written words are dropped, which is why the crossing that
// drops them owes a fetch.
uint16_t word_project(uint64_t mask)
{
  uint16_t words = 0;
  for (unsigned w = 0; w < 16; ++w) {
    if (((mask >> (4 * w)) & 0xF) == 0xF) {
      words = static_cast<uint16_t>(words | (1U << w));
    }
  }
  return words;
}

std::size_t log2_bucket(uint64_t v)
{
  std::size_t b = 0;
  while (v > 1 && b < 7) {
    v >>= 1;
    ++b;
  }
  return b;
}
} // namespace

std::deque<uint64_t>& materialize_pending()
{
  static std::deque<uint64_t> q;
  return q;
}

std::size_t mask_table_entries() { return table_entries(); }

namespace
{
// Every displacement of a live episode goes through here, so the accounting cannot drift
// between the three sites that can cause one.  Under `bound` the table is a pure
// instrument and overflow costs nothing -- that policy's job is to be the free-correctness
// upper bound.  Under the real policies the line has lost its mask, so it owes the fetch
// that makes it whole, and that fetch is queued as a real request rather than a counter.
void on_displacement(uint64_t displaced_line)
{
  auto& m = mask_stats();
  ++m.evicted_by_conflict;
  if (mask_live() > 0) {
    --mask_live();
  }
  if (knobs().partial_policy == knobs_t::partial_policy_t::bound) {
    return;
  }
  if (materialize_pending().size() >= MATERIALIZE_CAP) {
    ++m.materialize_dropped;
    return;
  }
  materialize_pending().push_back(displaced_line);
  ++m.overflow_materializes;
}
} // namespace

mask_stats_t& mask_stats()
{
  static mask_stats_t m{};
  return m;
}

// Tracking costs a table walk per far access, so it is only armed where its answer is
// meaningful: under the elide policy, whose safety is the thing in question.
bool mask_tracking_enabled() { return knobs().elide_store; }

bool mask_open(uint64_t line_addr)
{
  auto& m = mask_stats();
  bool displaced = false;
  uint64_t displaced_line = 0;
  mask_entry* e = mask_obtain(line_addr, displaced, displaced_line);
  if (e == nullptr) {
    ++m.admit_rejected; // admission control declined: no room without evicting a live episode
    return false;
  }
  if (displaced) {
    // Losing a live episode: report it, and charge the fetch it now owes, rather than
    // letting the displaced line's coverage vanish into the "fully covered" majority.
    on_displacement(displaced_line);
  }
  // Whatever the stores that provoked this grant already wrote stays: the grant does not
  // erase them, it only says the fetch they would have needed was skipped.
  if (e->granted) {
    // A second grant on an open episode merges two episodes' stores into one union,
    // which biases coverage upward.  It cannot be prevented here, but it must not be
    // silent (critic F6): elided_grants minus episodes_opened is this counter.
    ++m.regrant_on_open;
    return true; // already an open episode for this line
  }
  e->granted = true;

  ++m.episodes_opened;
  ++mask_live();
  m.live_max = std::max(m.live_max, mask_live());
  ++m.live_hist[log2_bucket(mask_live())];
  return true;
}

void mask_store(uint64_t byte_addr, unsigned char size)
{
  auto& m = mask_stats();
  const uint64_t line_addr = byte_addr & ~(LINE_BYTES - 1);
  const uint64_t off = byte_addr - line_addr;

  if (size != ACCESS_SIZE_NONE && size != ACCESS_SIZE_IRREGULAR) {
    // Sizes are recorded for every far store, tracked or not: the alignment distribution
    // is what decides whether a byte-granular mask is needed or a word-granular one will
    // do, and that question is about the workload, not about any one episode.
    const uint64_t len = (size == ACCESS_SIZE_OVERSIZE) ? LINE_BYTES : size;
    const std::size_t bucket = log2_bucket(len);
    ++m.far_store_size_hist[bucket];
    if ((byte_addr % len) == 0) {
      ++m.far_store_aligned[bucket];
    }
  }

  bool displaced = false;
  uint64_t displaced_line = 0;
  mask_entry* e = mask_obtain(line_addr, displaced, displaced_line);
  if (e == nullptr) {
    return; // admission control: the slot belongs to a live episode, leave it alone
  }
  if (displaced) {
    on_displacement(displaced_line);
  }
  ++m.stores_tracked;
  ++e->stores; // ordinal of this store on this line, so the completing one can be named

  if (size == ACCESS_SIZE_NONE) {
    ++m.stores_unsized;
    return;
  }

  if (size == ACCESS_SIZE_IRREGULAR) {
    e->strict_fail = 0xF; // no single extent: fails every granularity
    // A masked or scattered store has no single extent.  Treating it as 64 contiguous
    // bytes would make a partial write look full-line, which loosens exactly the bound
    // this measurement tightens, so it only marks the line as unjudgeable.
    e->irregular = true;
    return;
  }

  // ACCESS_SIZE_OVERSIZE covers at least the rest of this line, but its true extent is
  // unknown; crediting only what is certain keeps the coverage estimate from running high.
  const uint64_t len = (size == ACCESS_SIZE_OVERSIZE) ? (LINE_BYTES - off) : size;
  // Word-granularity fallback predicate (blueprint 4.2): a store that is not 4 B-aligned
  // in offset and length cannot be represented by word validity and would force a fetch.
  // Size alone cannot decide this -- an 8 B store at offset 2 passes a size test and
  // fails here -- which is why the store-level alignment histogram is not a substitute.
  if ((off % 4) != 0 || (len % 4) != 0) {
    e->subword = true;
  }
  for (unsigned g = 0; g < 4; ++g) {
    if ((off % GRANS[g]) != 0 || (len % GRANS[g]) != 0) {
      e->strict_fail |= static_cast<uint8_t>(1U << g);
    }
  }
  e->mask |= range_mask(off, len);
  if (e->full_at_store == 0 && e->mask == LINE_MASK_FULL) {
    e->full_at_store = e->stores;
  }

  if (off + len > LINE_BYTES) {
    // A store crossing the boundary writes both lines.  The model otherwise truncates the
    // address to a block number and loses the tail entirely -- 10% of 519.lbm_r's stores.
    ++m.straddling_stores;
    bool displaced_next = false;
    uint64_t displaced_line_next = 0;
    mask_entry* e_next = mask_obtain(line_addr + LINE_BYTES, displaced_next, displaced_line_next);
    if (e_next == nullptr) {
      return; // slot held by a live episode; the tail is not recorded
    }
    if (displaced_next) {
      on_displacement(displaced_line_next);
    }
    // The tail is a store on the next line too, so it takes an ordinal there.  Only the
    // per-line ordinal is incremented; stores_tracked already counted this store once.
    ++e_next->stores;
    const uint64_t tail = off + len - LINE_BYTES;
    if ((tail % 4) != 0) {
      e_next->subword = true; // tail offset is 0, so only its length can break alignment
    }
    for (unsigned g = 0; g < 4; ++g) {
      if ((tail % GRANS[g]) != 0) {
        e_next->strict_fail |= static_cast<uint8_t>(1U << g);
      }
    }
    e_next->mask |= range_mask(0, off + len - LINE_BYTES);
    if (e_next->full_at_store == 0 && e_next->mask == LINE_MASK_FULL) {
      e_next->full_at_store = e_next->stores;
    }
  }
}

void mask_load(uint64_t byte_addr, unsigned char size)
{
  if (size == ACCESS_SIZE_NONE) {
    return;
  }
  const uint64_t line_addr = byte_addr & ~(LINE_BYTES - 1);
  const uint64_t off = byte_addr - line_addr;
  const uint64_t len = (size == ACCESS_SIZE_IRREGULAR || size == ACCESS_SIZE_OVERSIZE) ? (LINE_BYTES - off) : size;

  if (mask_entry* e = mask_find(line_addr); e != nullptr) {
    // Reading a byte this episode never wrote is the other way elision breaks: the fetch
    // it skipped is needed after all, at this moment rather than at eviction.
    if ((range_mask(off, len) & ~e->mask) != 0) {
      e->read_uncovered = true;
    }
  }
  if (off + len > LINE_BYTES) {
    if (mask_entry* e_next = mask_find(line_addr + LINE_BYTES); e_next != nullptr) {
      if ((range_mask(0, off + len - LINE_BYTES) & ~e_next->mask) != 0) {
        e_next->read_uncovered = true;
      }
    }
  }
}

mask_verdict_t mask_peek(uint64_t line_addr)
{
  mask_verdict_t v{};
  // Serialization state first, and unconditionally: the episode may have been displaced
  // from the table while its merge read was on the link, and the eviction that issued
  // that read still has to wait for it.
  if (auto it = merge_inflight().find(line_addr); it != merge_inflight().end()) {
    v.merge_issued = true;
    v.merge_arrived = it->second;
  }
  mask_entry* e = mask_find(line_addr);
  if (e == nullptr || !e->granted) {
    return v;
  }
  v.tracked = true;
  v.complete = (__builtin_popcountll(e->mask) == static_cast<int>(LINE_BYTES)) && !e->irregular;
  return v;
}

// Called where far responses are consumed.  A merge fetch has no MSHR entry -- it was
// issued from the eviction path, not the miss path -- so its response has to be claimed
// before the MSHR search, which asserts on a miss.  Returns true exactly once per issued
// merge: the response is this eviction's data arriving, and the stalled writeback may go.
bool mask_merge_consume(uint64_t line_addr)
{
  auto it = merge_inflight().find(line_addr);
  if (it == merge_inflight().end() || it->second) {
    return false; // nothing issued, or already arrived: not ours to claim
  }
  it->second = true;
  return true;
}

// The writeback that waited on the merge has been sent; the protocol for this line is
// over.  Erasing here (not at consume) keeps the ARRIVED state visible to the stalled
// fill's retries in between.
void mask_merge_done(uint64_t line_addr) { merge_inflight().erase(line_addr); }

bool mask_episode_open(uint64_t line_addr)
{
  mask_entry* e = mask_find(line_addr);
  return e != nullptr && e->granted;
}

// [CXLGATE] A tracked line is leaving a level.  Evaluation happens on *every* crossing --
// a line can return to L1D and cross again -- while conversion happens at most once per
// episode without needing a flag, because a materialised mask is full and a full mask is
// clean at every granularity.
void mask_crossing(uint64_t line_addr, unsigned level, bool dirty)
{
  if (gate_mode() == 0) {
    return;
  }
  auto& m = mask_stats();
  mask_entry* e = mask_find(line_addr);
  if (e == nullptr || !e->granted) {
    // Not an episode: an ordinary write-allocate line, or a straddle tail that was touched
    // but never granted.  Its bytes are all valid, so it has no representation problem --
    // but it is counted, because "entry exists" would have swept it in (critic B2).
    if (level == 1) {
      ++m.crossings_untracked_l1;
    } else {
      ++m.crossings_untracked_l2;
    }
    return;
  }
  if (!dirty) {
    // An elide grant fills below L1D clean (the MSHR type is RFO, not WRITE), so a clean
    // victim carries no dirty bytes downward and there is nothing to represent (critic M3).
    if (level == 1) {
      ++m.crossings_clean_skipped_l1;
    } else {
      ++m.crossings_clean_skipped_l2;
    }
    return;
  }
  if (e->crossings < 255) {
    ++e->crossings;
  }

  if (level == 2) {
    // 4 B -> 4 B loses nothing, so this gate cannot fail.  What is checked instead is the
    // invariant that makes that true: every word recorded as descended is still written in
    // the byte mask (masks only grow).  A violation means the L1 snapshot is wrong, which
    // is the failure the level-1 gate would otherwise hide (critic B1/B4).
    ++m.crossings_l2;
    if ((word_project(e->mask) & e->l2_words) != e->l2_words) {
      ++m.crossings_l2_unclean;
    }
    return;
  }

  ++m.crossings_l1;
  if (g_clean(e->mask, 4) && !e->irregular) {
    e->l2_words = static_cast<uint16_t>(e->l2_words | word_project(e->mask));
    return;
  }
  ++m.crossings_l1_unclean;
  if (gate_mode() < 2) {
    return; // observe: the frequency is the answer, and behaviour must not move
  }
  if (materialize_pending().size() >= MATERIALIZE_CAP) {
    ++m.gate_materialize_dropped;
    return;
  }
  materialize_pending().push_back(line_addr);
  ++m.gate_materializes;
  // The fetch makes the line whole.  Recording that is both the truth and what stops this
  // gate from charging the same line twice.
  e->mask = LINE_MASK_FULL;
  e->irregular = false;
  e->l2_words = 0xFFFF;
  e->gate_materialized = true;
}

// LLC residency of granted lines, tracked as a plain +1/-1 at the single install point
// and the victim that install displaces.  Deliberately independent of mask_live(): that
// counts episodes (grant -> far writeback), this counts LLC occupancy, and H3 showed the
// two are not the same quantity.
void mask_llc_insert(uint64_t line_addr)
{
  if (mask_entry* e = mask_find(line_addr); e != nullptr && e->granted && !e->llc_resident) {
    e->llc_resident = true;
    auto& m = mask_stats();
    ++m.llc_resident_now;
    m.llc_resident_max = std::max(m.llc_resident_max, m.llc_resident_now);
  }
}

void mask_llc_evict(uint64_t line_addr, bool had_writeback)
{
  if (mask_entry* e = mask_find(line_addr); e != nullptr && e->llc_resident) {
    e->llc_resident = false;
    auto& m = mask_stats();
    if (m.llc_resident_now > 0) {
      --m.llc_resident_now;
    }
    if (!had_writeback) {
      ++m.llc_clean_drops; // the episode survives; its dirty bytes are still upstream
    }
  }
}

void mask_mark_merge_issued(uint64_t line_addr)
{
  // The writeback that follows a merge fetch can be refused and retried; without this the
  // retry would issue a second fetch for the same eviction and inflate the cost of the
  // pessimistic policy.  Kept in the dedicated map so table displacement cannot lose it.
  merge_inflight()[line_addr] = false;
}

void mask_close(uint64_t line_addr, bool sent_partial, bool merged_by_fetch)
{
  mask_entry* e = mask_find(line_addr);
  if (e == nullptr) {
    return;
  }
  if (!e->granted) {
    // Touched but never granted: an ordinary write-allocate line, whose coverage says
    // nothing about eliding a fetch.  Drop it without counting it as an episode.
    e->valid = false;
    return;
  }
  auto& m = mask_stats();
  const auto covered = static_cast<uint64_t>(__builtin_popcountll(e->mask));

  ++m.episodes_closed;
  m.covered_bytes_sum += covered;
  ++m.covered_hist[coverage_bucket(covered)];
  // 4 B word projection (blueprint 4, critic F4): the byte mask is discarded below, so the
  // word-granularity answer has to be taken here or the whole campaign runs twice.
  {
    uint64_t words = 0;
    for (unsigned w = 0; w < 16; ++w) {
      if (((e->mask >> (4 * w)) & 0xF) == 0xF) {
        ++words;
      }
    }
    ++m.covered_words_hist[words];
  }
  for (unsigned g = 0; g < 4; ++g) {
    if (g_clean(e->mask, GRANS[g])) {
      ++m.gran_representable[g];
    } else {
      ++m.gran_forced_fetch[g];
    }
    if ((e->strict_fail >> g) & 1U) {
      ++m.gran_strict_fallback[g];
    }
  }
  if (e->subword) {
    ++m.subword_episodes;
    if (!(covered == LINE_BYTES && !e->irregular)) {
      ++m.closed_partial_subword; // the intersection that actually costs a fallback
    }
  }
  // Attributed only for episodes that actually reached a writeback, so it divides by the
  // same denominator as closed_full rather than a larger one.
  if (e->full_at_store == 1) {
    ++m.full_on_first_store;
  } else if (e->full_at_store > 1) {
    ++m.partial_to_full_by_store;
  }
  // The verdict has to be taken here, at the writeback.  Asking whether the line was ever
  // covered over the whole program would answer a different and easier question.
  if (covered == LINE_BYTES && !e->irregular) {
    ++m.closed_full;
  } else {
    ++m.closed_partial;
  }
  if (e->irregular) {
    ++m.closed_irregular;
  }
  if (e->read_uncovered) {
    ++m.closed_read_uncovered;
  }
  if (sent_partial) {
    ++m.partial_writebacks;
  }
  if (merged_by_fetch) {
    ++m.merge_fetches;
  }
  // [CXLGATE] Censoring accounts (critic M8).  An episode that never left L1D was never
  // asked the question, and lumping it with "crossed and passed" would understate the
  // gate.  A converted episode closes full because the fetch made it so, so it has to be
  // named or the coverage number silently absorbs it.
  if (gate_mode() != 0) {
    ++m.crossing_hist[std::min<std::size_t>(e->crossings, 7)];
    if (e->crossings == 0) {
      ++m.episodes_no_crossing;
    }
    if (e->gate_materialized) {
      ++m.closed_gate_materialized;
    }
  }

  if (e->llc_resident) {
    e->llc_resident = false;
    if (m.llc_resident_now > 0) {
      --m.llc_resident_now;
    }
  }
  e->valid = false;
  if (mask_live() > 0) {
    --mask_live();
  }
}

void mask_reset()
{
  mask_stats() = mask_stats_t{};
  std::fill(std::begin(mask_table()), std::end(mask_table()), mask_entry{});
  mask_live() = 0;
  merge_inflight().clear();
  materialize_pending().clear();
}

void reset_stats()
{
  stats() = stats_t{};
  // [CXLMASK] Episodes opened during warmup would close in the measured phase and be
  // counted against it, so the table starts the phase empty too (17.9.4).
  mask_reset();
}

void print_stats()
{
  const auto& s = stats();
  fmt::print("\n=== CXLREPRO Statistics (simulation phase) ===\n");
  fmt::print("CXLREPRO store_policy: {}\n", knobs().nt_store ? "nt" : (knobs().elide_store ? "elide" : "allocate"));
  fmt::print("CXLREPRO nt_bypass_lines:   {:12}\n", s.nt_bypass_lines);
  fmt::print("CXLREPRO nt_bypass_retry:   {:12}\n", s.nt_bypass_retry);
  fmt::print("CXLREPRO far_rfo_fetches:   {:12}\n", s.far_rfo_fetches);
  fmt::print("CXLREPRO far_demand_reads:  {:12}\n", s.far_demand_reads);
  fmt::print("CXLREPRO far_prefetch_reads:{:12}\n", s.far_prefetch_reads);
  fmt::print("CXLREPRO far_writebacks:    {:12}\n", s.far_writebacks);
  fmt::print("CXLTX tx_period_ps:         {:12}\n", knobs().tx_period_ps);
  if (knobs().wr_bus_ratio > 1.0) { // [CXLASYM] printed only when armed
    fmt::print("CXLASYM wr_bus_ratio:       {:12.4f}\n", knobs().wr_bus_ratio);
  }
  if (knobs().elide_store) { // [CXLELIDE] printed only when armed: baseline output stays identical
    fmt::print("CXLELIDE elided_grants:     {:12}\n", s.elided_grants);
    const auto& m = mask_stats();
    fmt::print("CXLMASK episodes_opened:    {:12}\n", m.episodes_opened);
    fmt::print("CXLMASK episodes_closed:    {:12}\n", m.episodes_closed);
    fmt::print("CXLMASK closed_full:        {:12}\n", m.closed_full);
    fmt::print("CXLMASK closed_partial:     {:12}\n", m.closed_partial);
    fmt::print("CXLMASK closed_read_uncov:  {:12}\n", m.closed_read_uncovered);
    fmt::print("CXLMASK closed_irregular:   {:12}\n", m.closed_irregular);
    fmt::print("CXLMASK evicted_by_conflict:{:12}\n", m.evicted_by_conflict);
    fmt::print("CXLMASK stores_tracked:     {:12}\n", m.stores_tracked);
    fmt::print("CXLMASK stores_unsized:     {:12}\n", m.stores_unsized);
    fmt::print("CXLMASK straddling_stores:  {:12}\n", m.straddling_stores);
    fmt::print("CXLMASK full_on_first_store:{:12}\n", m.full_on_first_store);
    fmt::print("CXLMASK partial_to_full:    {:12}\n", m.partial_to_full_by_store);
    fmt::print("CXLMASK partial_writebacks: {:12}\n", m.partial_writebacks);
    fmt::print("CXLMASK merge_fetches:      {:12}\n", m.merge_fetches);
    fmt::print("CXLMASK partial_policy:     {:>12}\n",
               knobs().partial_policy == knobs_t::partial_policy_t::safe    ? "safe"
               : knobs().partial_policy == knobs_t::partial_policy_t::fetch ? "fetch"
                                                                            : "bound");
    fmt::print("CXLMASK live_max:           {:12}\n", m.live_max);
    // The episode ledger.  opened == closed + evicted_by_conflict + live_now must hold;
    // an unexplained residual means episodes vanished silently, and every silent loss so
    // far has biased coverage in the proposal's favor (critic F6/F14).
    fmt::print("CXLMASK live_now:           {:12}\n", mask_live());
    fmt::print("CXLMASK regrant_on_open:    {:12}\n", m.regrant_on_open);
    fmt::print("CXLMASK untracked_far_wb:   {:12}\n", m.untracked_far_writebacks);
    fmt::print("CXLMASK fetch_on_open_ep:   {:12}\n", m.fetch_on_open_episode);
    fmt::print("CXLMASK subword_episodes:   {:12}\n", m.subword_episodes);
    fmt::print("CXLMASK closed_partial_sub: {:12}\n", m.closed_partial_subword);
    fmt::print("CXLMASK llc_resident_max:   {:12}\n", m.llc_resident_max);
    fmt::print("CXLMASK llc_resident_now:   {:12}\n", m.llc_resident_now);
    fmt::print("CXLMASK llc_clean_drops:    {:12}\n", m.llc_clean_drops);
    for (unsigned g = 0; g < 4; ++g) {
      fmt::print("CXLMASK gran{}: representable {:12} forced_fetch {:12} strict_fallback {:12}\n", GRANS[g], m.gran_representable[g],
                 m.gran_forced_fetch[g], m.gran_strict_fallback[g]);
    }
    fmt::print("CXLMASK merge_unserialized: {:12}\n", m.merge_unserialized);
    fmt::print("CXLMASK merge_orphan_drop:  {:12}\n", m.merge_orphan_dropped);
    fmt::print("CXLMASK table_entries:      {:12}\n", table_entries());
    fmt::print("CXLMASK admit_mode:         {:12}\n", admit_mode() ? 1 : 0);
    fmt::print("CXLMASK admit_rejected:     {:12}\n", m.admit_rejected);
    fmt::print("CXLMASK overflow_material:  {:12}\n", m.overflow_materializes);
    fmt::print("CXLMASK material_dropped:   {:12}\n", m.materialize_dropped);
    // [CXLGATE] Printed only when armed, so a baseline run's output stays byte-identical.
    if (gate_mode() != 0) {
      fmt::print("CXLGATE mode:               {:12}\n", gate_mode());
      fmt::print("CXLGATE crossings_l1:       {:12}\n", m.crossings_l1);
      fmt::print("CXLGATE crossings_l1_uncln: {:12}\n", m.crossings_l1_unclean);
      fmt::print("CXLGATE crossings_l2:       {:12}\n", m.crossings_l2);
      fmt::print("CXLGATE crossings_l2_uncln: {:12}\n", m.crossings_l2_unclean);
      fmt::print("CXLGATE untracked_l1:       {:12}\n", m.crossings_untracked_l1);
      fmt::print("CXLGATE untracked_l2:       {:12}\n", m.crossings_untracked_l2);
      fmt::print("CXLGATE clean_skipped_l1:   {:12}\n", m.crossings_clean_skipped_l1);
      fmt::print("CXLGATE clean_skipped_l2:   {:12}\n", m.crossings_clean_skipped_l2);
      fmt::print("CXLGATE materializes:       {:12}\n", m.gate_materializes);
      fmt::print("CXLGATE material_dropped:   {:12}\n", m.gate_materialize_dropped);
      fmt::print("CXLGATE closed_materialized:{:12}\n", m.closed_gate_materialized);
      fmt::print("CXLGATE episodes_no_cross:  {:12}\n", m.episodes_no_crossing);
      for (std::size_t i = 0; i < 8; ++i) {
        fmt::print("CXLGATE crossing_hist[{}]:    {:12}\n", i, m.crossing_hist[i]);
      }
      if (m.crossings_l1 > 0) {
        fmt::print("CXLGATE unclean_rate_l1:    {:12.6f}\n",
                   static_cast<double>(m.crossings_l1_unclean) / static_cast<double>(m.crossings_l1));
      }
      // The two failures this instrument can have, both of which once presented as a
      // plausible number rather than an error (18.3.4, critic M2).
      if (m.episodes_opened > 0 && m.crossings_l1 == 0) {
        fmt::print("CXLGATE WARNING: episodes opened but no L1D crossing observed -- gate numbers are meaningless\n");
      }
      if (m.crossings_l2_unclean > 0) {
        fmt::print("CXLGATE WARNING: l2 descent invariant violated {} times -- the L1 snapshot is wrong\n", m.crossings_l2_unclean);
      }
    }
    if (m.episodes_closed > 0) {
      fmt::print("CXLMASK mean_covered_bytes: {:12.3f}\n", static_cast<double>(m.covered_bytes_sum) / static_cast<double>(m.episodes_closed));
      fmt::print("CXLMASK full_fraction:      {:12.4f}\n", static_cast<double>(m.closed_full) / static_cast<double>(m.episodes_closed));
    }
    // A coverage of zero has two very different causes and they must not look alike: the
    // stores really covered nothing, or nothing was ever observed.  The second was a live
    // bug here -- the store hook matched the cache by exact name while per-core caches
    // carry a prefix -- and it presented as a clean-looking full_fraction of 0.0000.
    if (m.episodes_opened > 0 && m.stores_tracked == 0) {
      fmt::print("CXLMASK WARNING: {} episodes opened but no store was observed on any of them; "
                 "the accumulation hook is not firing and the coverage figures above mean nothing\n",
                 m.episodes_opened);
    } else if (m.stores_unsized > 0) {
      // stores_unsized == stores_tracked means the trace carried no sizes, and every
      // coverage number above is then an artifact of that rather than a measurement.
      fmt::print("CXLMASK WARNING: {} of {} tracked stores had no size; coverage is not measurable on this trace "
                 "(use a size-carrying trace with --size-trace)\n",
                 m.stores_unsized, m.stores_tracked);
    }
    fmt::print("CXLMASK far_store_size_hist (1,2,4,8,16,32,64,>=128 B):");
    for (unsigned long i : m.far_store_size_hist) {
      fmt::print(" {}", i);
    }
    fmt::print("\nCXLMASK far_store_aligned   (same buckets):           ");
    for (unsigned long i : m.far_store_aligned) {
      fmt::print(" {}", i);
    }
    fmt::print("\nCXLMASK covered_hist (0,1-8,9-16,17-24,25-32,33-40,41-48,49-56,57-63,64):");
    for (unsigned long i : m.covered_hist) {
      fmt::print(" {}", i);
    }
    fmt::print("\nCXLMASK covered_words_hist (0..16 fully covered 4B words):");
    for (unsigned long i : m.covered_words_hist) {
      fmt::print(" {}", i);
    }
    fmt::print("\nCXLMASK live_hist (log2 of concurrent partial lines): ");
    for (unsigned long i : m.live_hist) {
      fmt::print(" {}", i);
    }
    fmt::print("\n");
  }
  if (far_mem() != nullptr) {
    fmt::print("CXLREPRO far_wq_backlog:    {:12}\n", far_mem()->pending_write_backlog());
  }
}
} // namespace cxl_repro
