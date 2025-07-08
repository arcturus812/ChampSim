#ifndef PAGE_STAT_H
#define PAGE_STAT_H

#include <cstdint>
#include <unordered_map>
#include <map>
#include <fmt/core.h>
#include <vector>

#define PHW_DEBUG false

/*
 * PAGE_STAT_EVENTs
 * USEFUL_PREFETCH include HIT
 */

enum PAGE_STAT_EVENT {
    HIT = 0,
    MISS = 1,
    USEFUL_PREFETCH = 2,
    PREFETCH = 3,
    USELESS_PREFETCH = 4,
    MSHR_PREFETCH_HIT = 5,
    MSHR_MISS_HANDLE = 6,
};

enum PAGE_STAT_CALLER {
    L1D = 1000,
    L2C = 2000,
    LLC = 3000
};

struct stats{
    uint64_t hit;
    uint64_t miss;

    // about prefetch
    uint64_t prefetch;
    uint64_t useful_prefetch_hit;
    uint64_t mshr_prefetch_hit;
    uint64_t pf_degree_sum;
    uint64_t pf_degree_cnt;
    uint64_t useless_prefetch;
    uint64_t accumulated_miss_handle_cycle;
    uint64_t accumulated_miss_handle_cnt;
}; 

struct page_stat{
    uint64_t pfn;
    uint64_t vfn;
    int cpu;
    stats *l1d;
    stats *l2c;
    stats *llc;

    bool mapped;
};

class page_stat_logger{
    public:
        page_stat_logger();
        void populate_page_stat_map(uint64_t num_pages, uint64_t base_address);
        bool map_pfn_to_vfn(uint64_t pfn, uint64_t vfn, int cpu);
        bool event_log(std::string caller, PAGE_STAT_EVENT event, uint64_t pfn, uint64_t vfn, int cpu, uint64_t value);
        void print_page_stat_map_to_csv();
    private:
        uint64_t num_populated_pages;
        uint64_t num_mapped_pages_dram;
        uint64_t num_mapped_pages_far;
        std::map<uint64_t, page_stat> page_stat_map; // pfn -> page_stat
        std::map<std::pair<uint64_t, int>, page_stat> reverse_map; // vfn, cpu -> page_stat
        std::map<std::pair<uint64_t, int>, page_stat> unmapped_stat_vfn;

        std::vector<uint64_t> l1d_prev_pf_addr;
        std::vector<uint64_t> l2c_prev_pf_addr;
        uint64_t llc_prev_pf_addr;
        std::vector<uint8_t> l1d_pf_degree;
        std::vector<uint8_t> l2c_pf_degree;
        uint8_t llc_pf_degree;

        page_stat* find_page_stat_by_pfn(uint64_t pfn);
        page_stat* find_unmapped_page_stat_by_vfn(uint64_t vfn, int cpu);
        page_stat* alloc_page_stat();
        bool free_unmapped_page_stat(page_stat* stat);
        void merge_page_stat(page_stat* src, page_stat* dst);

};

#endif // PAGE_STAT_H