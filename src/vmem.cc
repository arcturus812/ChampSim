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

#include "vmem.h"

#include <cassert>
#include <fmt/core.h>

#include "champsim.h"
#include "dram_controller.h"
#include "util/bits.h"

using namespace champsim::data::data_literals;

VirtualMemory::VirtualMemory(champsim::data::bytes page_table_page_size, std::size_t page_table_levels, champsim::chrono::clock::duration minor_penalty,
                             MEMORY_CONTROLLER& dram_, champsim::data::bytes dram_capacity_,
                             std::optional<uint64_t> randomization_seed_,
                             std::optional<champsim::data::bytes> physical_capacity_override)
    : randomization_seed(randomization_seed_), dram(dram_), physical_capacity(physical_capacity_override.value_or(dram_.size())),
      dram_capacity(dram_capacity_), minor_fault_penalty(minor_penalty), pt_levels(page_table_levels),
      pte_page_size(page_table_page_size),
      next_pte_page(
          champsim::dynamic_extent{champsim::data::bits{LOG2_PAGE_SIZE}, champsim::data::bits{champsim::lg2(champsim::data::bytes{pte_page_size}.count())}}, 0)
{
  assert(pte_page_size > 1_kiB);
  assert(champsim::is_power_of_2(pte_page_size.count()));

  champsim::page_number last_vpage{
      champsim::lowest_address_for_size(champsim::data::bytes{PAGE_SIZE + champsim::ipow(pte_page_size.count(), static_cast<unsigned>(pt_levels))})};
  champsim::data::bits required_bits{LOG2_PAGE_SIZE + champsim::lg2(last_vpage.to<uint64_t>())};
  if (required_bits > champsim::address::bits) {
    fmt::print("[VMEM] WARNING: virtual memory configuration would require {} bits of addressing.\n", required_bits); // LCOV_EXCL_LINE
  }
  if (required_bits > champsim::data::bits{champsim::lg2(physical_capacity.count())}) {
    fmt::print("[VMEM] WARNING: physical memory size is smaller than virtual memory size.\n"); // LCOV_EXCL_LINE
  }
  populate_pages();
  // shuffle_pages(); // [PHW] disable for classify dram and cxl address space
}

VirtualMemory::VirtualMemory(champsim::data::bytes page_table_page_size, std::size_t page_table_levels, champsim::chrono::clock::duration minor_penalty,
                             MEMORY_CONTROLLER& dram_, champsim::data::bytes dram_capacity_,
                             std::optional<champsim::data::bytes> physical_capacity_override)
    : VirtualMemory(page_table_page_size, page_table_levels, minor_penalty, dram_, dram_capacity_, {}, physical_capacity_override)
{
}

void VirtualMemory::populate_pages()
{
  assert(physical_capacity > 1_MiB);
  assert(dram_capacity > 1_MiB);
  assert(dram_capacity <= physical_capacity);
  
  champsim::data::bytes cxl_capacity = physical_capacity - dram_capacity;
  
  // DRAM 영역: 1MB ~ dram_capacity (page table용)
  std::size_t dram_pages = ((dram_capacity - 1_MiB) / PAGE_SIZE).count();
  ppage_free_list_dram.resize(dram_pages);
  
  champsim::page_number dram_base_address =
      champsim::page_number{champsim::lowest_address_for_size(
          std::max<champsim::data::mebibytes>(champsim::data::bytes{PAGE_SIZE}, 1_MiB))};
  
  for (auto it = ppage_free_list_dram.begin(); it != ppage_free_list_dram.end(); it++) {
    *it = dram_base_address;
    dram_base_address++;
  }
  
  // CXL 영역: dram_capacity ~ physical_capacity (data pages용)
  std::size_t cxl_pages = (cxl_capacity / PAGE_SIZE).count();
  ppage_free_list_cxl.resize(cxl_pages);
  
  champsim::page_number cxl_base_address =
      champsim::page_number{champsim::lowest_address_for_size(dram_capacity)};
  
  for (auto it = ppage_free_list_cxl.begin(); it != ppage_free_list_cxl.end(); it++) {
    *it = cxl_base_address;
    cxl_base_address++;
  }
  
  assert(ppage_free_list_dram.size() != 0);
  assert(ppage_free_list_cxl.size() != 0);
  
  fmt::print("[VMEM] Memory layout - DRAM (Page Table): {} pages, CXL (Data): {} pages\n", 
             ppage_free_list_dram.size(), ppage_free_list_cxl.size());
}

void VirtualMemory::shuffle_pages()
{
  // Shuffle disabled - pages are allocated sequentially from DRAM and CXL regions
}

champsim::dynamic_extent VirtualMemory::extent(std::size_t level) const
{
  const champsim::data::bits lower{LOG2_PAGE_SIZE + champsim::lg2(pte_page_size.count()) * (level - 1)};
  const auto size = static_cast<std::size_t>(champsim::lg2(pte_page_size.count()));
  return champsim::dynamic_extent{lower, size};
}

champsim::data::bits VirtualMemory::shamt(std::size_t level) const { return extent(level).lower; }

uint64_t VirtualMemory::get_offset(champsim::address vaddr, std::size_t level) const { return champsim::address_slice{extent(level), vaddr}.to<uint64_t>(); }

uint64_t VirtualMemory::get_offset(champsim::page_number vaddr, std::size_t level) const { return get_offset(champsim::address{vaddr}, level); }

// DRAM 영역 접근
champsim::page_number VirtualMemory::ppage_front_dram() const
{
  assert(available_ppages_dram() > 0);
  return ppage_free_list_dram.front();
}

void VirtualMemory::ppage_pop_dram()
{
  ppage_free_list_dram.pop_front();
}

std::size_t VirtualMemory::available_ppages_dram() const
{
  return ppage_free_list_dram.size();
}

// CXL 영역 접근
champsim::page_number VirtualMemory::ppage_front_cxl() const
{
  assert(available_ppages_cxl() > 0);
  return ppage_free_list_cxl.front();
}

void VirtualMemory::ppage_pop_cxl()
{
  ppage_free_list_cxl.pop_front();
}

std::size_t VirtualMemory::available_ppages_cxl() const
{
  return ppage_free_list_cxl.size();
}

// 호환성 함수들
champsim::page_number VirtualMemory::ppage_front() const
{
  // 하드코딩: true면 DRAM부터, false면 CXL부터 할당
  constexpr bool ALLOCATE_FROM_DRAM_FIRST = false;  // false = CXL부터, true = DRAM부터
  
  if (ALLOCATE_FROM_DRAM_FIRST) {
    if (available_ppages_dram() > 0) {
      return ppage_front_dram();
    }
    return ppage_front_cxl();
  } else {
    if (available_ppages_cxl() > 0) {
      return ppage_front_cxl();
    }
    return ppage_front_dram();
  }
}

void VirtualMemory::ppage_pop()
{
  // 하드코딩: true면 DRAM부터, false면 CXL부터 할당
  constexpr bool ALLOCATE_FROM_DRAM_FIRST = false;  // false = CXL부터, true = DRAM부터
  
  if (ALLOCATE_FROM_DRAM_FIRST) {
    if (available_ppages_dram() > 0) {
      ppage_pop_dram();
    } else {
      ppage_pop_cxl();
    }
  } else {
    if (available_ppages_cxl() > 0) {
      ppage_pop_cxl();
    } else {
      ppage_pop_dram();
    }
  }
  
  if (available_ppages() == 0) {
    fmt::print("[VMEM] WARNING: Out of physical memory, freeing ppages\n");
    populate_pages();
    // shuffle_pages(); // [PHW] disable for classify dram and cxl address space
  }
}

std::size_t VirtualMemory::available_ppages() const
{
  return available_ppages_dram() + available_ppages_cxl();
}

std::pair<champsim::page_number, champsim::chrono::clock::duration> VirtualMemory::va_to_pa(uint32_t cpu_num, champsim::page_number vaddr)
{
  // ppage_front()를 사용하여 하드코딩된 우선순위에 따라 할당
  auto [ppage, fault] = vpage_to_ppage_map.try_emplace({cpu_num, champsim::page_number{vaddr}}, ppage_front());

  // this vpage doesn't yet have a ppage mapping
  if (fault) {
    ppage_pop();
  }

  auto penalty = fault ? minor_fault_penalty : champsim::chrono::clock::duration::zero();

  if constexpr (champsim::debug_print) {
    uint64_t paddr_val = ppage->second.to<uint64_t>();
    constexpr bool ALLOCATE_FROM_DRAM_FIRST = false;
    bool is_dram = (paddr_val < dram_capacity.count());
    fmt::print("[VMEM] {} paddr: 0x{:x} ({}) vpage: {} fault: {}\n", 
               __func__, paddr_val, is_dram ? "DRAM" : "CXL", champsim::page_number{vaddr}, fault);
  }

  return std::pair{ppage->second, penalty};
}

std::pair<champsim::address, champsim::chrono::clock::duration> VirtualMemory::get_pte_pa(uint32_t cpu_num, champsim::page_number vaddr, std::size_t level)
{
  // DRAM 영역에서 할당
  if (champsim::page_offset{next_pte_page} == champsim::page_offset{0}) {
    active_pte_page = ppage_front_dram();
    ppage_pop_dram();
  }

  champsim::dynamic_extent pte_table_entry_extent{champsim::address::bits, shamt(level)};
  auto [ppage, fault] =
      page_table.try_emplace({cpu_num, level, champsim::address_slice{pte_table_entry_extent, vaddr}}, champsim::splice(active_pte_page, next_pte_page));

  // this PTE doesn't yet have a mapping
  if (fault) {
    next_pte_page++;
  }

  auto offset = get_offset(vaddr, level);
  champsim::address paddr{
      champsim::splice(ppage->second, champsim::address_slice{champsim::dynamic_extent{champsim::data::bits{champsim::lg2(pte_entry::byte_multiple)},
                                                                                       static_cast<std::size_t>(champsim::lg2(pte_page_size.count()))},
                                                              offset})};
  if constexpr (champsim::debug_print) {
    fmt::print("[VMEM] {} paddr: 0x{:x} (DRAM) vaddr: {} pt_page_offset: {} translation_level: {} fault: {}\n", __func__, paddr.to<uint64_t>(), vaddr, offset, level, fault);
  }

  auto penalty = minor_fault_penalty;
  if (!fault) {
    penalty = champsim::chrono::clock::duration::zero();
  }

  return {paddr, penalty};
}
