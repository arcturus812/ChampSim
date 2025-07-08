#ifndef PREFETCHER_STREAM_H
#define PREFETCHER_STREAM_H

#include <cstdint>

#include "address.h"
#include "modules.h"

struct stream : public champsim::modules::prefetcher {
  using prefetcher::prefetcher;
  
  // Stream prefetcher constants
  static constexpr std::size_t NUM_STREAM_BUFFER = 64;
  static constexpr std::size_t STREAM_BUFFER_SIZE = 8;
  static constexpr std::size_t STREAM_WINDOW = 16;
  static constexpr std::size_t PREF_CONFIDENCE = 2;
  static constexpr int PREF_DEGREE_MAX = 16;
  static constexpr int PREF_DEGREE_DEFAULT = 4;
  static constexpr double PREF_THRESHOLD = 0.5; // 50% of mshr

  // Stream buffer structure
  struct stream_buffer_t {
    uint64_t page;
    int direction;
    int confidence;
    int pf_idx;
    int degree;
  };

  // Statistics structure
  struct stream_stats_t {
    uint64_t num_pref;
    uint64_t num_useful;
    uint64_t num_to_l2c;
    uint64_t num_to_llc;
    double avg_mshr_occupancy_ratio;
  };

  // Member variables
  stream_buffer_t stream_buffers[NUM_STREAM_BUFFER];
  stream_stats_t stats;
  int replacement_idx;
  double total_occu;

  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);

  void prefetcher_initialize();
  // void prefetcher_branch_operate(champsim::address ip, uint8_t branch_type, champsim::address branch_target) {}
  void prefetcher_cycle_operate();
  void prefetcher_final_stats();
};

#endif
