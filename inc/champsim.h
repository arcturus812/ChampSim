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

#ifndef CHAMPSIM_H
#define CHAMPSIM_H

#include <cstdint>
#include <cstdlib>
#include <exception>
#include <limits>

#include "address.h"
#include "extent.h"
#include "util/bit_enum.h"
#include "util/ratio.h"
#include "util/units.h"

#include "page_stat.h"

extern const std::size_t NUM_CPUS;
extern const unsigned BLOCK_SIZE;
extern const unsigned PAGE_SIZE;
extern const unsigned LOG2_BLOCK_SIZE;
extern const unsigned LOG2_PAGE_SIZE;

extern const std::size_t DRAM_MAX_ADDR;
extern const bool ENABLE_PAGE_STATS;
extern page_stat_logger g_page_stat_logger;

namespace champsim
{
struct deadlock : public std::exception {
  const uint32_t which;
  explicit deadlock(uint32_t cpu) : which(cpu) {}
};

#ifdef DEBUG_PRINT
constexpr bool debug_print = true;
#else
constexpr bool debug_print = false;
#endif

template <typename Extent>
class address_slice;

/**
 * Convenience definitions for commmon address slices
 */
using address = address_slice<static_extent<champsim::data::bits{std::numeric_limits<uint64_t>::digits}, champsim::data::bits{}>>;
using block_number = address_slice<block_number_extent>;
using block_offset = address_slice<block_offset_extent>;
using page_number = address_slice<page_number_extent>;
using page_offset = address_slice<page_offset_extent>;

/**
 * Get the lowest possible address for which the space between it and zero is the given size.
 */
auto lowest_address_for_size(champsim::data::bytes sz) -> address;

/**
 * Get the lowest possible address for which the space between it and zero is the given bit width.
 */
auto lowest_address_for_width(champsim::data::bits width) -> address;
} // namespace champsim

inline bool is_far_addr(uint64_t addr)
{
  return addr >= DRAM_MAX_ADDR;
}

inline bool is_far_pfn(uint64_t pfn)
{
  return pfn >= (DRAM_MAX_ADDR >> LOG2_PAGE_SIZE);
}

#endif
