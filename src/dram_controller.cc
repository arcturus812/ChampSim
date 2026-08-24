/*
 *    Copyright 2023 The ChampSim Contributors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "dram_controller.h"

#include <algorithm>
#include <cfenv>
#include <cmath>
#include <fmt/core.h>

#include "deadlock.h"
#include "instruction.h"
#include "util/bits.h" // for lg2, bitmask
#include "util/span.h"
#include "util/units.h"

// [CXLMASK] CXL 1.0 Table 36: the byte-enable field is sent as its own data chunk, and only
// when it is not all ones.  A full-line write is four slots, a partial write five.  Kept as
// two named constants because the 5/4 ratio is a protocol fact, not a tuning knob.
namespace
{
constexpr long PARTIAL_WRITE_SLOTS = 5;
constexpr long FULL_WRITE_SLOTS = 4;
} // namespace

MEMORY_CONTROLLER::MEMORY_CONTROLLER(champsim::chrono::picoseconds dbus_period, champsim::chrono::picoseconds mc_period, std::size_t additional_cycle, std::size_t t_rp, std::size_t t_rcd,
                                     std::size_t t_cas, std::size_t t_ras, champsim::chrono::microseconds refresh_period, std::vector<channel_type*>&& ul,
                                     std::size_t rq_size, std::size_t wq_size, std::size_t chans, champsim::data::bytes chan_width, std::size_t rows,
                                     std::size_t columns, std::size_t ranks, std::size_t bankgroups, std::size_t banks, std::size_t refreshes_per_period,
                                     bool duplex_mode)
    : champsim::operable(mc_period), queues(std::move(ul)), channel_width(chan_width),
      address_mapping(chan_width, BLOCK_SIZE / chan_width.count(), chans, bankgroups, banks, columns, ranks, rows), data_bus_period(dbus_period)
{
  for (std::size_t i{0}; i < chans; ++i) {
    channels.emplace_back(dbus_period, mc_period, additional_cycle, t_rp, t_rcd, t_cas, t_ras, refresh_period, refreshes_per_period, chan_width, rq_size, wq_size,
                          address_mapping, duplex_mode);
  }
}

// [CXLTX] arm the shared transaction-rate budget on every channel of this controller
void MEMORY_CONTROLLER::set_tx_period(champsim::chrono::picoseconds period)
{
  for (auto& chan : channels) {
    chan.tx_period = period;
    chan.tx_cycle_available = champsim::chrono::clock::time_point{};
  }
}

// [CXLASYM] Slow the write direction relative to the read direction. ratio <= 1.0 restores
// the symmetric channel exactly.
void MEMORY_CONTROLLER::set_wr_bus_ratio(double ratio)
{
  for (auto& chan : channels) {
    chan.wr_bus_ratio = (ratio > 1.0) ? ratio : 1.0;
    chan.dbus_return_time_wr = std::chrono::duration_cast<champsim::chrono::clock::duration>(
        chan.DRAM_DBUS_RETURN_TIME * chan.wr_bus_ratio);
  }
}

DRAM_CHANNEL::DRAM_CHANNEL(champsim::chrono::picoseconds dbus_period, champsim::chrono::picoseconds mc_period, std::size_t t_add, std::size_t t_rp, std::size_t t_rcd,
                           std::size_t t_cas, std::size_t t_ras, champsim::chrono::microseconds refresh_period, std::size_t refreshes_per_period,
                           champsim::data::bytes width, std::size_t rq_size, std::size_t wq_size, DRAM_ADDRESS_MAPPING addr_mapper, bool duplex_)
    : champsim::operable(mc_period), address_mapping(addr_mapper), WQ{wq_size}, RQ{rq_size}, channel_width(width),
      DRAM_ROWS_PER_REFRESH(address_mapping.rows() / refreshes_per_period), tRP(t_rp * mc_period), tRCD(t_rcd * mc_period), tCAS(t_cas * mc_period),
      tRAS(t_ras * mc_period), tREF(refresh_period / refreshes_per_period),
      tRFC(std::chrono::duration_cast<champsim::chrono::clock::duration>(
          std::sqrt(champsim::data::bits_per_byte * (double)champsim::data::gibibytes{density()}.count()) * mc_period * t_ras)),
      DRAM_DBUS_TURN_AROUND_TIME(tRAS),
      DRAM_DBUS_RETURN_TIME(std::chrono::duration_cast<champsim::chrono::clock::duration>(dbus_period * address_mapping.prefetch_size)),
      DRAM_DBUS_BANKGROUP_STALL(
          std::chrono::duration_cast<champsim::chrono::clock::duration>((dbus_period * std::max(address_mapping.prefetch_size / 3, std::size_t{1})))),
      data_bus_period(dbus_period),
      tADD(t_add * mc_period),
      duplex(duplex_)
{
  dbus_return_time_wr = DRAM_DBUS_RETURN_TIME; // [CXLASYM] symmetric until told otherwise
  request_array_type br(address_mapping.ranks() * address_mapping.banks() * address_mapping.bankgroups());
  bank_request = br;
  active_request = std::end(bank_request);
  active_request_wr = std::end(bank_request);
  fmt::print("tADD: {} picoseconds duplex: {}\n", tADD.count(), duplex);
}

DRAM_ADDRESS_MAPPING::DRAM_ADDRESS_MAPPING(champsim::data::bytes channel_width_, std::size_t pref_size_, std::size_t channels_, std::size_t bankgroups_,
                                           std::size_t banks_, std::size_t columns_, std::size_t ranks_, std::size_t rows_)
    : address_slicer(make_slicer(channel_width_, pref_size_, channels_, bankgroups_, banks_, columns_, ranks_, rows_)), prefetch_size(pref_size_)
{
  // assert prefetch size is not zero
  assert(prefetch_size != 0);
  // assert prefetch size is multiple of block size
  assert((channel_width_.count() * prefetch_size) % BLOCK_SIZE == 0);

  // mapping sanity check
  assert(columns() >= 1 && columns() == columns_);
  assert(rows() >= 1 && rows() == rows_);
  assert(banks() >= 1 && banks() == banks_);
  assert(bankgroups() >= 1 && bankgroups() == bankgroups_);
  assert(ranks() >= 1 && ranks() == ranks_);
  assert(channels() >= 1 && channels() == channels_);
}

auto DRAM_ADDRESS_MAPPING::make_slicer(champsim::data::bytes channel_width, std::size_t pref_size, std::size_t channels, std::size_t bankgroups,
                                       std::size_t banks, std::size_t columns, std::size_t ranks, std::size_t rows) -> slicer_type
{
  std::array<std::size_t, slicer_type::size()> params{};
  params.at(SLICER_ROW_IDX) = rows;
  params.at(SLICER_COLUMN_IDX) = columns / pref_size;
  params.at(SLICER_RANK_IDX) = ranks;
  params.at(SLICER_BANK_IDX) = banks;
  params.at(SLICER_BANKGROUP_IDX) = bankgroups;
  params.at(SLICER_CHANNEL_IDX) = channels;
  params.at(SLICER_OFFSET_IDX) = channel_width.count() * pref_size;
  return std::apply([](auto... p) { return champsim::make_contiguous_extent_set(0, champsim::lg2(p)...); }, params);
}

long MEMORY_CONTROLLER::operate()
{
  long progress{0};

  initiate_requests();

  for (auto& channel : channels) {
    progress += channel._operate();
  }

  return progress;
}

long DRAM_CHANNEL::operate()
{
  long progress{0};

  if (warmup) {
    for (auto& entry : RQ) {
      if (entry.has_value()) {
        response_type response{entry->address, entry->v_address, entry->data, entry->pf_metadata, entry->instr_depend_on_me};
        for (auto* ret : entry.value().to_return) {
          ret->push_back(response);
        }

        ++progress;
        entry.reset();
      }
    }

    for (auto& entry : WQ) {
      if (entry.has_value()) {
        ++progress;
      }
      entry.reset();
    }
  }

  check_write_collision();
  check_read_collision();
  if (duplex) {
    // [CXLREPRO] full-duplex link: independent read and write data buses, no
    // write-mode switching and no turnaround. Banks (device-internal media)
    // remain shared between both directions.
    progress += finish_dbus_request_on(active_request);
    progress += finish_dbus_request_on(active_request_wr);
    progress += schedule_refresh();
    progress += populate_dbus_duplex();
    // Alternate which direction schedules into banks first each cycle. Bank
    // slots are held from scheduling until bus completion (tADD-dominated),
    // so a fixed read-first order lets a full RQ camp on every freed bank and
    // starve writebacks behind an unbounded upstream queue.
    const bool wq_first = ((current_time.time_since_epoch() / clock_period) % 2) != 0;
    progress += service_packet(schedule_packet_from(wq_first));
    progress += service_packet(schedule_packet_from(!wq_first));
  } else {
    progress += finish_dbus_request();
    swap_write_mode();
    progress += schedule_refresh();
    progress += populate_dbus();
    progress += service_packet(schedule_packet());
  }

  return progress;
}

long DRAM_CHANNEL::finish_dbus_request() { return finish_dbus_request_on(active_request); }

long DRAM_CHANNEL::finish_dbus_request_on(request_array_type::iterator& slot)
{
  long progress{0};

  if (slot != std::end(bank_request) && slot->ready_time <= current_time) {
    response_type response{slot->pkt->value().address, slot->pkt->value().v_address, slot->pkt->value().data,
                           slot->pkt->value().pf_metadata, slot->pkt->value().instr_depend_on_me};
    for (auto* ret : slot->pkt->value().to_return) {
      ret->push_back(response);
    }

    // [CXLREPRO] per-direction completion accounting
    if (slot->pkt->value().is_write) {
      ++sim_stats.WR_LINES;
    } else {
      ++sim_stats.RD_LINES;
    }

    slot->valid = false;

    slot->pkt->reset();
    slot = std::end(bank_request);
    ++progress;
  }

  return progress;
}

long DRAM_CHANNEL::schedule_refresh()
{
  long progress = {0};
  // check if we reached refresh cycle

  bool schedule_refresh = current_time >= last_refresh + tREF;
  // if so, record stats
  if (schedule_refresh) {
    last_refresh = current_time;
    refresh_row += DRAM_ROWS_PER_REFRESH;
    sim_stats.refresh_cycles++;
    if (refresh_row >= address_mapping.rows())
      refresh_row -= address_mapping.rows();
  }

  // go through each bank, and handle refreshes
  for (auto& b_req : bank_request) {
    // refresh is now needed for this bank
    if (schedule_refresh) {
      b_req.need_refresh = true;
    }
    // refresh is being scheduled for this bank
    if (b_req.need_refresh && !b_req.valid) {
      b_req.ready_time = current_time + tRFC;
      b_req.need_refresh = false;
      b_req.under_refresh = true;
    }
    // refresh is done for this bank
    else if (b_req.under_refresh && b_req.ready_time <= current_time) {
      b_req.under_refresh = false;
      b_req.open_row.reset();
      progress++;
    }

    if (b_req.under_refresh)
      progress++;
  }
  return (progress);
}

void DRAM_CHANNEL::swap_write_mode()
{
  // these values control when to send out a burst of writes
  const std::size_t DRAM_WRITE_HIGH_WM = ((std::size(WQ) * 7) >> 3); // 7/8th
  const std::size_t DRAM_WRITE_LOW_WM = ((std::size(WQ) * 6) >> 3);  // 6/8th
  // const std::size_t MIN_DRAM_WRITES_PER_SWITCH = ((std::size(WQ) * 1) >> 2); // 1/4

  // Check queue occupancy
  auto wq_occu = static_cast<std::size_t>(std::count_if(std::begin(WQ), std::end(WQ), [](const auto& x) { return x.has_value(); }));
  auto rq_occu = static_cast<std::size_t>(std::count_if(std::begin(RQ), std::end(RQ), [](const auto& x) { return x.has_value(); }));

  // Change modes if the queues are unbalanced
  if ((!write_mode && (wq_occu >= DRAM_WRITE_HIGH_WM || (rq_occu == 0 && wq_occu > 0)))
      || (write_mode && (wq_occu == 0 || (rq_occu > 0 && wq_occu < DRAM_WRITE_LOW_WM)))) {
    // Reset scheduled requests
    for (auto it = std::begin(bank_request); it != std::end(bank_request); ++it) {
      // Leave active request on the data bus
      if (it != active_request && it->valid) {
        // Leave rows charged
        if (it->ready_time < (current_time + tCAS)) {
          it->open_row.reset();
        }

        // This bank is ready for another DRAM request
        it->valid = false;
        it->pkt->value().scheduled = false;
        it->pkt->value().ready_time = current_time;
      }
    }

    // Add data bus turn-around time
    if (active_request != std::end(bank_request)) {
      dbus_cycle_available = active_request->ready_time + DRAM_DBUS_TURN_AROUND_TIME; // After ongoing finish
    } else {
      dbus_cycle_available = current_time + DRAM_DBUS_TURN_AROUND_TIME;
    }

    // Invert the mode
    write_mode = !write_mode;
  }
}

// Look for requests to put on the bus
long DRAM_CHANNEL::populate_dbus()
{
  long progress{0};

  auto iter_next_process = std::min_element(std::begin(bank_request), std::end(bank_request),
                                            [](const auto& lhs, const auto& rhs) { return !rhs.valid || (lhs.valid && lhs.ready_time < rhs.ready_time); });
  if (iter_next_process->valid && iter_next_process->ready_time <= current_time) {
    if (active_request == std::end(bank_request) && dbus_cycle_available <= current_time) {
      // Bus is available
      // Put this request on the data bus

      // get which bankgroup we are in
      auto op_bankgroup = bankgroup_request_index(iter_next_process->pkt->value().address);
      auto bankgroup_ready_time = bankgroup_readytime[op_bankgroup];

      active_request = iter_next_process;

      // set return time. Incur penalty if bankgroup is on cooldown
      if (bankgroup_ready_time > current_time)
        active_request->ready_time = bankgroup_ready_time + DRAM_DBUS_RETURN_TIME;
      else
        active_request->ready_time = current_time + DRAM_DBUS_RETURN_TIME;

      // set when bankgroup dbus will be next ready
      bankgroup_readytime[op_bankgroup] = current_time + DRAM_DBUS_RETURN_TIME + DRAM_DBUS_BANKGROUP_STALL;

      // [CXLREPRO] bus occupancy accounting (legacy shared bus: one direction at a time)
      if (active_request->pkt->value().is_write) {
        sim_stats.wr_bus_busy_ps += DRAM_DBUS_RETURN_TIME.count();
      } else {
        sim_stats.rd_bus_busy_ps += DRAM_DBUS_RETURN_TIME.count();
      }

      if (iter_next_process->row_buffer_hit) {
        if (write_mode) {
          ++sim_stats.WQ_ROW_BUFFER_HIT;
        } else {
          ++sim_stats.RQ_ROW_BUFFER_HIT;
        }
      } else if (write_mode) {
        ++sim_stats.WQ_ROW_BUFFER_MISS;
      } else {
        ++sim_stats.RQ_ROW_BUFFER_MISS;
      }

      ++progress;
    } else {
      // Bus is congested
      if (active_request != std::end(bank_request)) {
        sim_stats.dbus_cycle_congested += (active_request->ready_time - current_time) / data_bus_period;
      } else {
        sim_stats.dbus_cycle_congested += (dbus_cycle_available - current_time) / data_bus_period;
      }
      ++sim_stats.dbus_count_congested;
    }
  }

  return progress;
}

// [CXLREPRO] duplex-mode bus population: each direction has its own bus slot and
// availability clock. A bank request's direction is given by its packet's
// is_write tag, so a request being transferred on one bus can never be picked
// by the other. Bankgroup cooldown (device-internal media constraint) is shared
// between directions, which naturally models a total-media ceiling below the
// sum of both direction ceilings.
long DRAM_CHANNEL::populate_dbus_duplex()
{
  long progress{0};

  // [CXLTX] Alternate which direction gets first refusal. With a shared transaction
  // budget the first-served direction would otherwise win every contested slot.
  for (bool dir_write : {tx_prefer_write, !tx_prefer_write}) {
    auto& slot = dir_write ? active_request_wr : active_request;
    auto& avail = dir_write ? dbus_cycle_available_wr : dbus_cycle_available;

    auto dir_valid = [dir_write](const BANK_REQUEST& br) {
      return br.valid && br.pkt->value().is_write == dir_write;
    };
    auto iter_next_process = std::min_element(std::begin(bank_request), std::end(bank_request), [&dir_valid](const auto& lhs, const auto& rhs) {
      return !dir_valid(rhs) || (dir_valid(lhs) && lhs.ready_time < rhs.ready_time);
    });

    // [CXLTX] A serviced line consumes one transaction slot regardless of direction.
    const bool tx_budget_ok = (tx_period == champsim::chrono::picoseconds{0}) || (tx_cycle_available <= current_time);

    if (iter_next_process != std::end(bank_request) && dir_valid(*iter_next_process) && iter_next_process->ready_time <= current_time && !tx_budget_ok) {
      // The data bus may be free, but the shared transaction budget is not. Charge the
      // wait here so it is attributable, and do not consume a bus slot.
      ++sim_stats.tx_stall_events;
      sim_stats.tx_stall_ps += (tx_cycle_available - current_time).count();
      continue;
    }

    if (iter_next_process != std::end(bank_request) && dir_valid(*iter_next_process) && iter_next_process->ready_time <= current_time) {
      if (slot == std::end(bank_request) && avail <= current_time) {
        // This direction's bus is available: put the request on it
        auto op_bankgroup = bankgroup_request_index(iter_next_process->pkt->value().address);
        auto bankgroup_ready_time = bankgroup_readytime[op_bankgroup];

        slot = iter_next_process;

        // [CXLASYM] the write direction may transfer a line more slowly than the read one
        auto return_time = dir_write ? dbus_return_time_wr : DRAM_DBUS_RETURN_TIME;

        // [CXLMASK] A partial write puts a byte-enable chunk on the wire ahead of the data,
        // five slots instead of four.  It costs no extra transaction -- that is the whole
        // point of MemWrPtl -- so modelling only transactions would make the safe policy
        // identical to the unsafe one by construction, and the comparison would prove
        // nothing.  The cost is here, in time on the link, where it can actually be seen.
        if (dir_write && iter_next_process->pkt->value().partial_write) {
          return_time = (return_time * PARTIAL_WRITE_SLOTS) / FULL_WRITE_SLOTS;
          ++sim_stats.partial_write_lines;
        }

        if (bankgroup_ready_time > current_time) {
          slot->ready_time = bankgroup_ready_time + return_time;
        } else {
          slot->ready_time = current_time + return_time;
        }

        bankgroup_readytime[op_bankgroup] = current_time + return_time + DRAM_DBUS_BANKGROUP_STALL;

        // [CXLTX] consume the shared transaction slot and hand priority to the other direction
        if (tx_period != champsim::chrono::picoseconds{0}) {
          tx_cycle_available = current_time + tx_period;
          ++sim_stats.tx_grants;
          tx_prefer_write = !dir_write;
        }

        if (dir_write) {
          sim_stats.wr_bus_busy_ps += return_time.count();
        } else {
          sim_stats.rd_bus_busy_ps += return_time.count();
        }

        if (iter_next_process->row_buffer_hit) {
          if (dir_write) {
            ++sim_stats.WQ_ROW_BUFFER_HIT;
          } else {
            ++sim_stats.RQ_ROW_BUFFER_HIT;
          }
        } else if (dir_write) {
          ++sim_stats.WQ_ROW_BUFFER_MISS;
        } else {
          ++sim_stats.RQ_ROW_BUFFER_MISS;
        }

        ++progress;
      } else {
        // This direction's bus is congested
        if (slot != std::end(bank_request)) {
          sim_stats.dbus_cycle_congested += (slot->ready_time - current_time) / data_bus_period;
        } else {
          sim_stats.dbus_cycle_congested += (avail - current_time) / data_bus_period;
        }
        ++sim_stats.dbus_count_congested;
      }
    }
  }

  return progress;
}

std::size_t DRAM_CHANNEL::bank_request_index(champsim::address addr) const
{
  auto op_bank = address_mapping.get_bank(addr);

  return (bankgroup_request_index(addr) * address_mapping.banks() + op_bank);
}

std::size_t DRAM_CHANNEL::bankgroup_request_index(champsim::address addr) const
{
  auto op_rank = address_mapping.get_rank(addr);
  auto op_bankgroup = address_mapping.get_bankgroup(addr);

  return (op_rank * address_mapping.bankgroups() + op_bankgroup);
}

// Look for queued packets that have not been scheduled
DRAM_CHANNEL::queue_type::iterator DRAM_CHANNEL::schedule_packet()
{
  // Look for queued packets that have not been scheduled
  // prioritize packets that are ready to execute, bank is free
  auto next_schedule = [this](const auto& lhs, const auto& rhs) {
    if (!(rhs.has_value() && !rhs.value().scheduled)) {
      return true;
    }
    if (!(lhs.has_value() && !lhs.value().scheduled)) {
      return false;
    }

    auto lop_idx = this->bank_request_index(lhs.value().address);
    auto rop_idx = this->bank_request_index(rhs.value().address);
    auto rready = !this->bank_request[rop_idx].valid;
    auto lready = !this->bank_request[lop_idx].valid;
    return (rready == lready) ? lhs.value().ready_time <= rhs.value().ready_time : lready;
  };
  queue_type::iterator iter_next_schedule;
  if (write_mode) {
    iter_next_schedule = std::min_element(std::begin(WQ), std::end(WQ), next_schedule);
  } else {
    iter_next_schedule = std::min_element(std::begin(RQ), std::end(RQ), next_schedule);
  }
  return (iter_next_schedule);
}

// [CXLREPRO] schedule from an explicit queue, independent of write_mode
DRAM_CHANNEL::queue_type::iterator DRAM_CHANNEL::schedule_packet_from(bool from_wq)
{
  auto next_schedule = [this](const auto& lhs, const auto& rhs) {
    if (!(rhs.has_value() && !rhs.value().scheduled)) {
      return true;
    }
    if (!(lhs.has_value() && !lhs.value().scheduled)) {
      return false;
    }

    auto lop_idx = this->bank_request_index(lhs.value().address);
    auto rop_idx = this->bank_request_index(rhs.value().address);
    auto rready = !this->bank_request[rop_idx].valid;
    auto lready = !this->bank_request[lop_idx].valid;
    return (rready == lready) ? lhs.value().ready_time <= rhs.value().ready_time : lready;
  };
  if (from_wq) {
    return std::min_element(std::begin(WQ), std::end(WQ), next_schedule);
  }
  return std::min_element(std::begin(RQ), std::end(RQ), next_schedule);
}

long DRAM_CHANNEL::service_packet(DRAM_CHANNEL::queue_type::iterator pkt)
{
  long progress{0};
  if (pkt->has_value() && pkt->value().ready_time <= current_time) {
    auto op_row = address_mapping.get_row(pkt->value().address);
    auto op_idx = bank_request_index(pkt->value().address);

    if (!bank_request[op_idx].valid && !bank_request[op_idx].under_refresh) {
      bool row_buffer_hit = (bank_request[op_idx].open_row.has_value() && *(bank_request[op_idx].open_row) == op_row);

      // this bank is now busy
      auto row_charge_delay = champsim::chrono::clock::duration{bank_request[op_idx].open_row.has_value() ? tRP + tRCD : tRCD};
      bank_request[op_idx] = {true,  row_buffer_hit,        false,
                              false, std::optional{op_row}, current_time + tADD + tCAS + (row_buffer_hit ? champsim::chrono::clock::duration{} : row_charge_delay),
                              pkt};
      // bank_request[op_idx] = {true,  row_buffer_hit,        false,
      //                         false, std::optional{op_row}, current_time + tCAS + (row_buffer_hit ? champsim::chrono::clock::duration{} : row_charge_delay),
      //                         pkt};
      pkt->value().scheduled = true;
      pkt->value().ready_time = champsim::chrono::clock::time_point::max();

      ++progress;
    }
  }

  return progress;
}

void MEMORY_CONTROLLER::initialize()
{
  using namespace champsim::data::data_literals;
  using namespace std::literals::chrono_literals;
  auto sz = this->size();
  if (champsim::data::gibibytes gb_sz{sz}; gb_sz > 1_GiB) {
    fmt::print("Off-chip DRAM Size: {}", gb_sz);
  } else if (champsim::data::mebibytes mb_sz{sz}; mb_sz > 1_MiB) {
    fmt::print("Off-chip DRAM Size: {}", mb_sz);
  } else if (champsim::data::kibibytes kb_sz{sz}; kb_sz > 1_kiB) {
    fmt::print("Off-chip DRAM Size: {}", kb_sz);
  } else {
    fmt::print("Off-chip DRAM Size: {}", sz);
  }
  fmt::print(" Channels: {} Width: {}-bit Data Rate: {} MT/s\n", std::size(channels), champsim::data::bits_per_byte * channel_width.count(),
             1us / (data_bus_period));
}

void DRAM_CHANNEL::initialize() {}

void MEMORY_CONTROLLER::begin_phase()
{
  std::size_t chan_idx = 0;
  for (auto& chan : channels) {
    DRAM_CHANNEL::stats_type new_stats;
    new_stats.name = channel_name_prefix + std::to_string(chan_idx++);
    chan.sim_stats = new_stats;
    chan.warmup = warmup;
  }

  for (auto* ul : queues) {
    channel_type::stats_type ul_new_roi_stats;
    channel_type::stats_type ul_new_sim_stats;
    ul->roi_stats = ul_new_roi_stats;
    ul->sim_stats = ul_new_sim_stats;
  }
}

void DRAM_CHANNEL::begin_phase() {}

void MEMORY_CONTROLLER::end_phase(unsigned cpu)
{
  for (auto& chan : channels) {
    chan.end_phase(cpu);
  }
}

void DRAM_CHANNEL::end_phase(unsigned /*cpu*/) { roi_stats = sim_stats; }

bool DRAM_ADDRESS_MAPPING::is_collision(champsim::address a, champsim::address b) const
{
  // collision if everything but offset matches
  champsim::data::bits offset_bits = champsim::data::bits{champsim::size(get<SLICER_OFFSET_IDX>(address_slicer))};
  return (a.slice_upper(offset_bits) == b.slice_upper(offset_bits));
}

void DRAM_CHANNEL::check_write_collision()
{
  for (auto wq_it = std::begin(WQ); wq_it != std::end(WQ); ++wq_it) {
    if (wq_it->has_value() && !wq_it->value().forward_checked) {
      auto checker = [addr_map = address_mapping, check_val = wq_it->value().address](const auto& pkt) {
        return pkt.has_value() && addr_map.is_collision(pkt.value().address, check_val);
      };

      auto found = std::find_if(std::begin(WQ), wq_it, checker); // Forward check
      if (found == wq_it) {
        found = std::find_if(std::next(wq_it), std::end(WQ), checker); // Backward check
      }

      if (found != std::end(WQ)) {
        wq_it->reset();
      } else {
        wq_it->value().forward_checked = true;
      }
    }
  }
}

void DRAM_CHANNEL::check_read_collision()
{
  for (auto rq_it = std::begin(RQ); rq_it != std::end(RQ); ++rq_it) {
    if (rq_it->has_value() && !rq_it->value().forward_checked) {
      auto checker = [addr_map = address_mapping, check_val = rq_it->value().address](const auto& x) {
        return x.has_value() && addr_map.is_collision(x.value().address, check_val);
      };
      // write forward
      if (auto wq_it = std::find_if(std::begin(WQ), std::end(WQ), checker); wq_it != std::end(WQ)) {
        response_type response{rq_it->value().address, rq_it->value().v_address, wq_it->value().data, rq_it->value().pf_metadata,
                               rq_it->value().instr_depend_on_me};
        for (auto* ret : rq_it->value().to_return) {
          ret->push_back(response);
        }

        rq_it->reset();

      }
      // backwards check
      else if (auto found = std::find_if(std::begin(RQ), rq_it, checker); found != rq_it) {
        auto instr_copy = std::move(found->value().instr_depend_on_me);
        auto ret_copy = std::move(found->value().to_return);

        std::set_union(std::begin(instr_copy), std::end(instr_copy), std::begin(rq_it->value().instr_depend_on_me), std::end(rq_it->value().instr_depend_on_me),
                       std::back_inserter(found->value().instr_depend_on_me));
        std::set_union(std::begin(ret_copy), std::end(ret_copy), std::begin(rq_it->value().to_return), std::end(rq_it->value().to_return),
                       std::back_inserter(found->value().to_return));

        rq_it->reset();

      }
      // forwards check
      else if (found = std::find_if(std::next(rq_it), std::end(RQ), checker); found != std::end(RQ)) {
        auto instr_copy = std::move(found->value().instr_depend_on_me);
        auto ret_copy = std::move(found->value().to_return);

        std::set_union(std::begin(instr_copy), std::end(instr_copy), std::begin(rq_it->value().instr_depend_on_me), std::end(rq_it->value().instr_depend_on_me),
                       std::back_inserter(found->value().instr_depend_on_me));
        std::set_union(std::begin(ret_copy), std::end(ret_copy), std::begin(rq_it->value().to_return), std::end(rq_it->value().to_return),
                       std::back_inserter(found->value().to_return));

        rq_it->reset();
      } else {
        rq_it->value().forward_checked = true;
      }
    }
  }
}

void MEMORY_CONTROLLER::initiate_requests()
{
  // Initiate read requests
  for (auto* ul : queues) {
    for (auto q : {std::ref(ul->RQ), std::ref(ul->PQ)}) {
      auto [begin, end] = champsim::get_span_p(std::cbegin(q.get()), std::cend(q.get()), [ul, this](const auto& pkt) { return this->add_rq(pkt, ul); });
      q.get().erase(begin, end);
    }

    // Initiate write requests
    auto [wq_begin, wq_end] = champsim::get_span_p(std::cbegin(ul->WQ), std::cend(ul->WQ), [this](const auto& pkt) { return this->add_wq(pkt); });
    ul->WQ.erase(wq_begin, wq_end);
  }
}

DRAM_CHANNEL::request_type::request_type(const typename champsim::channel::request_type& req)
    : partial_write(req.partial_write), pf_metadata(req.pf_metadata), address(req.address), v_address(req.address), data(req.data),
      instr_depend_on_me(req.instr_depend_on_me)
{
  asid[0] = req.asid[0];
  asid[1] = req.asid[1];
}

bool MEMORY_CONTROLLER::add_rq(const request_type& packet, champsim::channel* ul)
{
  auto& channel = channels[address_mapping.get_channel(packet.address)];

  if (auto rq_it = std::find_if_not(std::begin(channel.RQ), std::end(channel.RQ), [this](const auto& pkt) { return pkt.has_value(); });
      rq_it != std::end(channel.RQ)) {
    *rq_it = DRAM_CHANNEL::request_type{packet};
    rq_it->value().forward_checked = false;
    rq_it->value().scheduled = false;
    rq_it->value().is_write = false; // [CXLREPRO]
    rq_it->value().ready_time = current_time;
    if (packet.response_requested)
      rq_it->value().to_return = {&ul->returned};

    return true;
  }

  return false;
}

bool MEMORY_CONTROLLER::add_wq(const request_type& packet)
{
  auto& channel = channels[address_mapping.get_channel(packet.address)];

  // search for the empty index
  if (auto wq_it = std::find_if_not(std::begin(channel.WQ), std::end(channel.WQ), [](const auto& pkt) { return pkt.has_value(); });
      wq_it != std::end(channel.WQ)) {
    *wq_it = DRAM_CHANNEL::request_type{packet};
    wq_it->value().forward_checked = false;
    wq_it->value().scheduled = false;
    wq_it->value().is_write = true; // [CXLREPRO]
    wq_it->value().ready_time = current_time;

    return true;
  }

  ++channel.sim_stats.WQ_FULL;
  return false;
}

unsigned long DRAM_ADDRESS_MAPPING::swizzle_bits(champsim::address address, unsigned long segment_size, champsim::data::bits segment_offset,
                                                 unsigned long field, unsigned long field_bits) const
{
  champsim::address_slice row{get<SLICER_ROW_IDX>(address_slicer), address};
  unsigned long permute_field = field;

  for (champsim::dynamic_extent subextent{champsim::data::bits{0}, segment_size}; subextent.upper <= row.upper_extent();
       subextent = champsim::dynamic_extent{subextent.upper, segment_size}) {
    permute_field ^= row.slice(subextent).slice(champsim::dynamic_extent{segment_offset, field_bits}).to<unsigned long>();
  }
  return permute_field;
}

unsigned long DRAM_ADDRESS_MAPPING::get_channel(champsim::address address) const
{
  unsigned long channel = std::get<SLICER_CHANNEL_IDX>(address_slicer(address)).to<unsigned long>();
  // channel bits should be xor'd with each row bit
  unsigned long c_bits = champsim::size(get<SLICER_CHANNEL_IDX>(address_slicer));
  return (swizzle_bits(address, 1, champsim::data::bits{0}, channel, c_bits));
}
unsigned long DRAM_ADDRESS_MAPPING::get_rank(champsim::address address) const { return std::get<SLICER_RANK_IDX>(address_slicer(address)).to<unsigned long>(); }
unsigned long DRAM_ADDRESS_MAPPING::get_bankgroup(champsim::address address) const
{
  unsigned long bankgroup = std::get<SLICER_BANKGROUP_IDX>(address_slicer(address)).to<unsigned long>();

  unsigned long bg_bits = champsim::size(get<SLICER_BANKGROUP_IDX>(address_slicer));
  unsigned long bk_bits = champsim::size(get<SLICER_BANK_IDX>(address_slicer));
  return (swizzle_bits(address, bg_bits + bk_bits, champsim::data::bits{0}, bankgroup, bg_bits));
}
unsigned long DRAM_ADDRESS_MAPPING::get_bank(champsim::address address) const
{
  unsigned long bank = std::get<SLICER_BANK_IDX>(address_slicer(address)).to<unsigned long>();

  unsigned long bg_bits = champsim::size(get<SLICER_BANKGROUP_IDX>(address_slicer));
  unsigned long bk_bits = champsim::size(get<SLICER_BANK_IDX>(address_slicer));
  // bank bits should be xor'd with select row bits

  return (swizzle_bits(address, bg_bits + bk_bits, champsim::data::bits{bg_bits}, bank, bk_bits));
}
unsigned long DRAM_ADDRESS_MAPPING::get_row(champsim::address address) const { return std::get<SLICER_ROW_IDX>(address_slicer(address)).to<unsigned long>(); }
unsigned long DRAM_ADDRESS_MAPPING::get_column(champsim::address address) const
{
  return std::get<SLICER_COLUMN_IDX>(address_slicer(address)).to<unsigned long>();
}

std::size_t MEMORY_CONTROLLER::pending_write_backlog() const
{
  std::size_t n = 0;
  for (const auto* ul : queues) {
    n += std::size(ul->WQ);
  }
  for (const auto& chan : channels) {
    // a WQ entry stays occupied from insertion through bank scheduling and bus
    // transfer until completion resets it, so WQ occupancy covers in-flight writes
    n += static_cast<std::size_t>(std::count_if(std::begin(chan.WQ), std::end(chan.WQ), [](const auto& e) { return e.has_value(); }));
  }
  return n;
}

champsim::data::bytes MEMORY_CONTROLLER::size() const { return champsim::data::bytes{(1ll << address_mapping.address_slicer.bit_size())}; }
champsim::data::bytes DRAM_CHANNEL::density() const
{
  return champsim::data::bytes{(long long)(address_mapping.rows() * address_mapping.columns() * address_mapping.banks() * address_mapping.bankgroups())};
}

std::size_t DRAM_ADDRESS_MAPPING::rows() const { return std::size_t{1} << champsim::size(get<SLICER_ROW_IDX>(address_slicer)); }
std::size_t DRAM_ADDRESS_MAPPING::columns() const { return prefetch_size << champsim::size(get<SLICER_COLUMN_IDX>(address_slicer)); }
std::size_t DRAM_ADDRESS_MAPPING::ranks() const { return std::size_t{1} << champsim::size(get<SLICER_RANK_IDX>(address_slicer)); }
std::size_t DRAM_ADDRESS_MAPPING::bankgroups() const { return std::size_t{1} << champsim::size(get<SLICER_BANKGROUP_IDX>(address_slicer)); }
std::size_t DRAM_ADDRESS_MAPPING::banks() const { return std::size_t{1} << champsim::size(get<SLICER_BANK_IDX>(address_slicer)); }
std::size_t DRAM_ADDRESS_MAPPING::channels() const { return std::size_t{1} << champsim::size(get<SLICER_CHANNEL_IDX>(address_slicer)); }
std::size_t DRAM_CHANNEL::bank_request_capacity() const { return std::size(bank_request); }
std::size_t DRAM_CHANNEL::bankgroup_request_capacity() const { return std::size(bankgroup_readytime); };

// LCOV_EXCL_START Exclude the following function from LCOV
void MEMORY_CONTROLLER::print_deadlock()
{
  int j = 0;
  for (auto& chan : channels) {
    fmt::print("DRAM Channel {}\n", j++);
    chan.print_deadlock();
  }
}

void DRAM_CHANNEL::print_deadlock()
{
  std::string_view q_writer{"address: {} forward_checked: {} scheduled: {}"};
  auto q_entry_pack = [](const auto& entry) {
    return std::tuple{entry->address, entry->forward_checked, entry->scheduled};
  };

  champsim::range_print_deadlock(RQ, "RQ", q_writer, q_entry_pack);
  champsim::range_print_deadlock(WQ, "WQ", q_writer, q_entry_pack);
}
// LCOV_EXCL_STOP
