#include "cxl_repro.h"

#include <cstdlib>
#include <cstring>
#include <fmt/core.h>

#include "dram_controller.h"

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

void reset_stats() { stats() = stats_t{}; }

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
  }
  if (far_mem() != nullptr) {
    fmt::print("CXLREPRO far_wq_backlog:    {:12}\n", far_mem()->pending_write_backlog());
  }
}
} // namespace cxl_repro
