#include "dram_stats.h"

dram_stats operator-(dram_stats lhs, dram_stats rhs)
{
  lhs.dbus_cycle_congested -= rhs.dbus_cycle_congested;
  lhs.dbus_count_congested -= rhs.dbus_count_congested;
  lhs.WQ_ROW_BUFFER_HIT -= rhs.WQ_ROW_BUFFER_HIT;
  lhs.WQ_ROW_BUFFER_MISS -= rhs.WQ_ROW_BUFFER_MISS;
  lhs.RQ_ROW_BUFFER_HIT -= rhs.RQ_ROW_BUFFER_HIT;
  lhs.RQ_ROW_BUFFER_MISS -= rhs.RQ_ROW_BUFFER_MISS;
  lhs.WQ_FULL -= rhs.WQ_FULL;
  lhs.RD_LINES -= rhs.RD_LINES;
  lhs.WR_LINES -= rhs.WR_LINES;
  lhs.tx_grants -= rhs.tx_grants;             // [CXLTX]
  lhs.tx_stall_events -= rhs.tx_stall_events; // [CXLTX]
  lhs.tx_stall_ps -= rhs.tx_stall_ps;         // [CXLTX]
  lhs.rd_bus_busy_ps -= rhs.rd_bus_busy_ps;
  lhs.wr_bus_busy_ps -= rhs.wr_bus_busy_ps;
  return lhs;
}
