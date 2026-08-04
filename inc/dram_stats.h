#ifndef DRAM_STATS_H
#define DRAM_STATS_H

#include <cstdint>
#include <string>

struct dram_stats {
  std::string name{};
  long dbus_cycle_congested{};
  uint64_t dbus_count_congested = 0;
  uint64_t refresh_cycles = 0;
  unsigned WQ_ROW_BUFFER_HIT = 0, WQ_ROW_BUFFER_MISS = 0, RQ_ROW_BUFFER_HIT = 0, RQ_ROW_BUFFER_MISS = 0, WQ_FULL = 0;

  // [CXLREPRO] per-direction accounting (valid in both duplex and legacy bus modes)
  uint64_t RD_LINES = 0, WR_LINES = 0;                 // lines completed per direction
  long long rd_bus_busy_ps = 0, wr_bus_busy_ps = 0;    // data-bus occupancy per direction, picoseconds

  // [CXLTX] shared transaction-rate budget accounting.
  // tx_grants must equal RD_LINES + WR_LINES: every serviced line consumes exactly
  // one transaction slot, whatever direction it travels and whatever data it carries.
  uint64_t tx_grants = 0;        // transaction slots consumed
  uint64_t tx_stall_events = 0;  // bus grants deferred because the budget was exhausted
  long long tx_stall_ps = 0;     // cumulative wait attributable to the budget
};

dram_stats operator-(dram_stats lhs, dram_stats rhs);

#endif
