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

#include <algorithm>
#include <cassert>
#include <iostream>
#include <numeric>
#include <random>

#include "champsim.h"
#include "util.h"

VirtualMemory::VirtualMemory(uint64_t capacity, uint64_t pg_size, uint32_t page_table_levels, uint64_t random_seed, uint64_t minor_fault_penalty)
    : minor_fault_penalty(minor_fault_penalty), pt_levels(page_table_levels), page_size(pg_size),
      ppage_free_list(((capacity * PMEM_FAST_RATE) - VMEM_RESERVE_CAPACITY) / PAGE_SIZE, PAGE_SIZE), ppage_free_list_slow((capacity * PMEM_SLOW_RATE) / PAGE_SIZE, PAGE_SIZE) 
{
  assert(capacity % PAGE_SIZE == 0);
  assert(pg_size == (1ul << lg2(pg_size)) && pg_size > 1024);
  capacity_fast = ((capacity * PMEM_FAST_RATE) - VMEM_RESERVE_CAPACITY) / PAGE_SIZE;
  capacity_slow = (capacity * PMEM_SLOW_RATE) / PAGE_SIZE;

  // populate the free list
  ppage_free_list.front() = VMEM_RESERVE_CAPACITY; //[PHW] Why store capacity in the first element?
  std::partial_sum(std::cbegin(ppage_free_list), std::cend(ppage_free_list), std::begin(ppage_free_list));
  // test code
  // auto it = ppage_free_list.end() - 1;
  // std::cout << "TEST" << std::endl;
  // std::cout << capacity << std::endl; //
  // std::cout << capacity_fast*PAGE_SIZE << std::endl; //
  // std::cout << VMEM_RESERVE_CAPACITY << std::endl; //
  // std::cout << *it << std::endl; // when fast rate is 0.25, this value is 2147479552
  ppage_free_list_slow.front() = ppage_free_list.back(); //[PHW] continue address space
  fast_phys_addr_end = ppage_free_list.back();
  std::partial_sum(std::cbegin(ppage_free_list_slow), std::cend(ppage_free_list_slow), std::begin(ppage_free_list_slow)); //[PHW] populate slow mem page list
  // test code
  // std::cout << "size of fast\t" << capacity_fast*PAGE_SIZE << std::endl; //
  // std::cout << *std::cbegin(ppage_free_list) << std::endl;
  // std::cout << ppage_free_list.back() << std::endl;
  // std::cout << "size of slow\t" << capacity_slow*PAGE_SIZE << std::endl; //
  // std::cout << *std::cbegin(ppage_free_list_slow) << std::endl;
  // std::cout << ppage_free_list_slow.back() << std::endl;

  // then shuffle it
  // [PHW] why we need to shuffle? for realistic experiment?
  std::shuffle(std::begin(ppage_free_list), std::end(ppage_free_list), std::mt19937_64{random_seed});
  std::shuffle(std::begin(ppage_free_list_slow), std::end(ppage_free_list_slow), std::mt19937_64{random_seed}); //[PHW] shuffle slow meme too

  // [PHW] use fast-memory first, ppage_free_list_slow should be used later
  next_pte_page = ppage_free_list.front();
  ppage_free_list.pop_front();
}

uint64_t VirtualMemory::shamt(uint32_t level) const { return LOG2_PAGE_SIZE + lg2(page_size / PTE_BYTES) * (level); }

uint64_t VirtualMemory::get_offset(uint64_t vaddr, uint32_t level) const { return (vaddr >> shamt(level)) & bitmask(lg2(page_size / PTE_BYTES)); }

std::pair<uint64_t, bool> VirtualMemory::va_to_pa(uint32_t cpu_num, uint64_t vaddr)
{
  auto it = vpage_to_ppage_map.find({cpu_num, vaddr >> LOG2_PAGE_SIZE});
  if( it != vpage_to_ppage_map.end()){ // target key found in fast-mem
    auto [ppage, fault] = vpage_to_ppage_map.insert({{cpu_num, vaddr >> LOG2_PAGE_SIZE}, ppage_free_list.front()});
    if (fault)
      ppage_free_list.pop_front();
    return {splice_bits(ppage->second, vaddr, LOG2_PAGE_SIZE), fault};
  }else{ // target key is not found in fast-mem
    if(ppage_free_list.size() > 0){ // fast memory is not full, use fast memory page
      auto [ppage, fault] = vpage_to_ppage_map.insert({{cpu_num, vaddr >> LOG2_PAGE_SIZE}, ppage_free_list.front()});
      if (fault)
        ppage_free_list.pop_front();
      return {splice_bits(ppage->second, vaddr, LOG2_PAGE_SIZE), fault};
    }else{ // fast memory is full, use slow memory apge
      auto [ppage, fault] = vpage_to_ppage_map.insert({{cpu_num, vaddr >> LOG2_PAGE_SIZE}, ppage_free_list_slow.front()});
      if (fault)
        ppage_free_list_slow.pop_front();
      return {splice_bits(ppage->second, vaddr, LOG2_PAGE_SIZE), fault};
    }
  }
}

std::pair<uint64_t, bool> VirtualMemory::get_pte_pa(uint32_t cpu_num, uint64_t vaddr, uint32_t level)
{
  std::tuple key{cpu_num, vaddr >> shamt(level + 1), level};
  auto [ppage, fault] = page_table.insert({key, next_pte_page});

  // this PTE doesn't yet have a mapping
  if (fault) {
    next_pte_page += page_size;
    if (next_pte_page % PAGE_SIZE) {
      // [PHW] check fast-mem full?
      if(ppage_free_list.size() > 0){
        next_pte_page = ppage_free_list.front();
        ppage_free_list.pop_front();
      }else{
        next_pte_page = ppage_free_list_slow.front();
        ppage_free_list_slow.pop_front();
      }
    }
  }

  return {splice_bits(ppage->second, get_offset(vaddr, level) * PTE_BYTES, lg2(page_size)), fault};
}
