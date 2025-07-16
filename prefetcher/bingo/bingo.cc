#include "bingo.h"
#include <iostream>
#include <cassert>

void bingo::prefetcher_initialize() {
    // Initialize statistics or other setup if needed
    std::cout << "Initializing Bingo Prefetcher" << std::endl;
    std::cout << "Pattern length: " << PATTERN_LEN << std::endl;
    std::cout << "Region size: " << REGION_SIZE << std::endl;
}

uint32_t bingo::prefetcher_cache_operate(champsim::address addr, champsim::address ip, 
                                          uint8_t cache_hit, bool useful_prefetch, 
                                          access_type type, uint32_t metadata_in) {
    // Convert address to block number (assuming 64-byte blocks)
    uint64_t block_number = addr.to<uint64_t>() / 64;
    uint64_t region_number = block_number / PATTERN_LEN;
    int region_offset = static_cast<int>(block_number % PATTERN_LEN);
    
    champsim::address region_addr{region_number};
    
    // Try to set pattern in accumulation table
    bool success = accumulation_table.set_pattern(region_addr, region_offset);
    
    if (success) {
        return metadata_in;
    }
    
    // Check filter table
    FilterTableEntry* entry = filter_table.find(region_addr);
    
    if (!entry) {
        // Trigger access - insert into filter table
        filter_table.insert(region_addr, ip, region_offset);
        
        // Search in pattern history table
        std::vector<bool> pattern = find_in_phts(ip, champsim::address{block_number});
        
        if (pattern.empty()) {
            return metadata_in;
        }
        
        // Issue prefetches based on pattern
        for (unsigned i = 0; i < PATTERN_LEN; i++) {
            if (pattern[i]) {
                champsim::address pf_addr{(region_number * PATTERN_LEN + i) * 64};
                prefetch_line(pf_addr, true, metadata_in);
            }
        }
        
        return metadata_in;
    }
    
    if (entry->offset != region_offset) {
        // Move from filter table to accumulation table
        AccumulationTableEntry victim = accumulation_table.insert(*entry);
        accumulation_table.set_pattern(region_addr, region_offset);
        
        filter_table.erase(region_addr);
        
        if (victim.key.to<uint64_t>() != 0) {
            // Move from accumulation table to PHT
            insert_in_phts(victim);
        }
    }
    
    return metadata_in;
}

uint32_t bingo::prefetcher_cache_fill(champsim::address addr, long set, long way, 
                                       uint8_t prefetch, champsim::address evicted_addr, 
                                       uint32_t metadata_in) {
    // No special handling needed for cache fill in basic implementation
    return metadata_in;
}

void bingo::prefetcher_cycle_operate() {
    // Periodic operations if needed
}

void bingo::prefetcher_final_stats() {
    // Print final statistics
    std::cout << "Bingo Prefetcher Final Statistics" << std::endl;
    std::cout << filter_table.log() << std::endl;
    std::cout << accumulation_table.log() << std::endl;
    std::cout << pht.log() << std::endl;
}

// Helper functions
std::vector<bool> bingo::find_in_phts(champsim::address pc, champsim::address address) {
    return pht.find(pc, address);
}

void bingo::insert_in_phts(const AccumulationTableEntry& entry) {
    pht.insert(entry.pc, champsim::address{entry.key.to<uint64_t>() * PATTERN_LEN + entry.offset}, 
               entry.pattern);
}
