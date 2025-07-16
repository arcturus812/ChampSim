#ifndef PREFETCHER_BINGO_H
#define PREFETCHER_BINGO_H

#include <cstdint>
#include <vector>
#include <map>
#include <iomanip>

#include "address.h"
#include "modules.h"
#include "msl/lru_table.h"
#include "cache.h"

// Filter Table Entry
struct FilterTableEntry {
    champsim::address key;
    champsim::address pc;
    int offset;
    
    FilterTableEntry() : key(0), pc(0), offset(0) {}
    FilterTableEntry(champsim::address k, champsim::address p, int o) : key(k), pc(p), offset(o) {}
    
    auto index() const { 
        using namespace champsim::data::data_literals;
        return key.slice_lower<4_b>(); 
    }
    auto tag() const { return key; }
};

// Accumulation Table Entry
struct AccumulationTableEntry {
    champsim::address key;
    champsim::address pc;
    int offset;
    std::vector<bool> pattern;
    
    AccumulationTableEntry() : key(0), pc(0), offset(0) {}
    AccumulationTableEntry(champsim::address k, champsim::address p, int o, unsigned pattern_len) 
        : key(k), pc(p), offset(o), pattern(pattern_len, false) {}
    
    auto index() const { 
        using namespace champsim::data::data_literals;
        return key.slice_lower<4_b>(); 
    }
    auto tag() const { return key; }
};

// Pattern History Table Entry
struct PatternHistoryTableEntry {
    champsim::address key;
    std::vector<bool> pattern;
    
    PatternHistoryTableEntry() : key(0) {}
    PatternHistoryTableEntry(champsim::address k, unsigned pattern_len) : key(k), pattern(pattern_len, false) {}
    
    auto index() const { 
        using namespace champsim::data::data_literals;
        return key.slice_lower<9_b>(); 
    }
    auto tag() const { return key; }
};

// Filter Table
class FilterTable {
private:
    static constexpr std::size_t FILTER_TABLE_SETS = 16;
    static constexpr std::size_t FILTER_TABLE_WAYS = 16;
    
    champsim::msl::lru_table<FilterTableEntry> ft_cache;

public:
    FilterTable() : ft_cache(FILTER_TABLE_SETS, FILTER_TABLE_WAYS) {}
    
    FilterTableEntry* find(champsim::address region_number) {
        FilterTableEntry query(region_number, champsim::address{0}, 0);
        auto hit = ft_cache.check_hit(query);
        if (hit.has_value()) {
            return &hit.value();
        }
        return nullptr;
    }
    
    void insert(champsim::address region_number, champsim::address pc, int offset) {
        FilterTableEntry entry(region_number, pc, offset);
        ft_cache.fill(entry);
    }
    
    void erase(champsim::address region_number) {
        // Mark as invalid by setting offset to -1
        FilterTableEntry entry(region_number, champsim::address{0}, -1);
        ft_cache.fill(entry);
    }
    
    std::string log() const {
        return "[FilterTable] entries\n";
    }
};

// Accumulation Table
class AccumulationTable {
private:
    static constexpr std::size_t ACCUMULATION_TABLE_SETS = 16;
    static constexpr std::size_t ACCUMULATION_TABLE_WAYS = 16;
    
    unsigned pattern_len;
    champsim::msl::lru_table<AccumulationTableEntry> at_cache;

public:
    AccumulationTable(unsigned p_len) 
        : pattern_len(p_len), at_cache(ACCUMULATION_TABLE_SETS, ACCUMULATION_TABLE_WAYS) {}
    
    bool set_pattern(champsim::address region_number, int offset) {
        AccumulationTableEntry query(region_number, champsim::address{0}, 0, pattern_len);
        auto hit = at_cache.check_hit(query);
        if (hit.has_value()) {
            // Create a new entry with the updated pattern
            AccumulationTableEntry updated_entry = hit.value();
            if (offset >= 0 && offset < static_cast<int>(updated_entry.pattern.size())) {
                updated_entry.pattern[offset] = true;
                at_cache.fill(updated_entry);
            }
            return true;
        }
        return false;
    }
    
    AccumulationTableEntry insert(const FilterTableEntry& entry) {
        AccumulationTableEntry query(entry.key, champsim::address{0}, 0, pattern_len);
        auto victim = at_cache.check_hit(query);
        AccumulationTableEntry old_entry;
        if (victim.has_value()) {
            old_entry = victim.value();
        }
        
        AccumulationTableEntry new_entry(entry.key, entry.pc, entry.offset, pattern_len);
        if (entry.offset >= 0 && entry.offset < static_cast<int>(new_entry.pattern.size())) {
            new_entry.pattern[entry.offset] = true;
        }
        at_cache.fill(new_entry);
        return old_entry;
    }
    
    std::string log() const {
        return "[AccumulationTable] entries\n";
    }
};

// Pattern History Table
class PatternHistoryTable {
private:
    static constexpr std::size_t PHT_SETS = 512;
    static constexpr std::size_t PHT_WAYS = 4;
    
    unsigned pattern_len;
    unsigned min_addr_width;
    unsigned max_addr_width;
    unsigned pc_width;
    unsigned index_len;
    int num_sets;
    
    champsim::msl::lru_table<PatternHistoryTableEntry> pht_cache;

public:
    PatternHistoryTable(unsigned p_len, unsigned min_aw, 
                       unsigned max_aw, unsigned pc_w)
        : pattern_len(p_len), min_addr_width(min_aw),
          max_addr_width(max_aw), pc_width(pc_w),
          num_sets(PHT_SETS), pht_cache(PHT_SETS, PHT_WAYS) {
        index_len = __builtin_ctz(static_cast<unsigned>(num_sets));
    }
    
    void insert(champsim::address pc, champsim::address address, 
                const std::vector<bool>& pattern) {
        champsim::address key = build_key(pc, address);
        PatternHistoryTableEntry entry(key, pattern_len);
        entry.pattern = pattern;
        pht_cache.fill(entry);
    }
    
    std::vector<bool> find(champsim::address pc, champsim::address address) {
        champsim::address key = build_key(pc, address);
        PatternHistoryTableEntry query(key, pattern_len);
        
        auto hit = pht_cache.check_hit(query);
        if (hit.has_value()) {
            return hit.value().pattern;
        }
        return std::vector<bool>();
    }
    
    std::string log() const {
        return "[PatternHistoryTable] entries\n";
    }

private:
    champsim::address build_key(champsim::address pc, champsim::address address) {
        uint64_t pc_val = pc.to<uint64_t>() & ((1ULL << pc_width) - 1);
        uint64_t addr_val = address.to<uint64_t>() & ((1ULL << max_addr_width) - 1);
        uint64_t offset = addr_val & ((1ULL << min_addr_width) - 1);
        uint64_t base = (addr_val >> min_addr_width);
        
        uint64_t key = (base << (pc_width + min_addr_width)) | 
                       (pc_val << min_addr_width) | offset;
        
        // Simple CRC-like hashing
        uint64_t tag = ((pc_val << min_addr_width) | offset);
        while (tag > 0) {
            tag >>= index_len;
            key ^= tag & ((1ULL << index_len) - 1);
        }
        
        return champsim::address{key};
    }
    
    std::vector<bool> vote(const std::vector<std::vector<bool>>& x, float thresh = 0.2f) {
        auto n = static_cast<int>(x.size());
        std::vector<bool> ret(pattern_len, false);
        
        for (unsigned i = 0; i < pattern_len; i++) {
            int cnt = 0;
            for (int j = 0; j < n; j++) {
                if (j < static_cast<int>(x.size()) && i < x[j].size() && x[j][i]) {
                    cnt++;
                }
            }
            if (n > 0 && (static_cast<float>(cnt) / static_cast<float>(n)) >= thresh) {
                ret[i] = true;
            }
        }
        return ret;
    }
};

// Main Bingo Prefetcher Class
struct bingo : public champsim::modules::prefetcher {
    // Constants
    static constexpr unsigned PC_WIDTH = 16;
    static constexpr unsigned MAX_ADDR_WIDTH = 16;
    static constexpr unsigned MIN_ADDR_WIDTH = 4;
    static constexpr unsigned REGION_SIZE = 2048;  // 2KB region
    static constexpr float PHT_VOTE_THRESHOLD = 0.2f;
    static constexpr unsigned PATTERN_LEN = REGION_SIZE / 64;  // 64 is block size
    
    // Tables
    FilterTable filter_table;
    AccumulationTable accumulation_table;
    PatternHistoryTable pht;
    
    // Constructor
    explicit bingo(CACHE* cache) 
        : champsim::modules::prefetcher(cache), 
          accumulation_table(PATTERN_LEN),
          pht(PATTERN_LEN, MIN_ADDR_WIDTH, MAX_ADDR_WIDTH, PC_WIDTH) {}
    
    // Main functions
    void prefetcher_initialize();
    uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, 
                                      uint8_t cache_hit, bool useful_prefetch, 
                                      access_type type, uint32_t metadata_in);
    uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, 
                                   uint8_t prefetch, champsim::address evicted_addr, 
                                   uint32_t metadata_in);
    void prefetcher_cycle_operate();
    void prefetcher_final_stats();

private:
    // Helper functions
    std::vector<bool> find_in_phts(champsim::address pc, champsim::address address);
    void insert_in_phts(const AccumulationTableEntry& entry);
};

#endif // PREFETCHER_BINGO_H
