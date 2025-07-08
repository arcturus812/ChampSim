#include "stream.h"

void stream::prefetcher_initialize() {
  // Initialize stream prefetcher
  for(std::size_t i = 0; i < NUM_STREAM_BUFFER; i++){
    stream_buffers[i].page = 0;
    stream_buffers[i].direction = 0;
    stream_buffers[i].confidence = 0;
    stream_buffers[i].pf_idx = -1;
    stream_buffers[i].degree = PREF_DEGREE_DEFAULT;
  }
  
  // Initialize statistics
  stats.num_to_l2c = 0;
  stats.num_to_llc = 0;
  stats.num_pref = 0;
  stats.num_useful = 0;
  stats.avg_mshr_occupancy_ratio = 0;
  total_occu = 0;

  replacement_idx = 0;
}

uint32_t stream::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                          uint32_t metadata_in)
{
  uint64_t page = addr.to<uint64_t>() >> LOG2_PAGE_SIZE;
  uint64_t page_offset = (addr.to<uint64_t>() >> LOG2_BLOCK_SIZE) & ((PAGE_SIZE / BLOCK_SIZE) - 1);
  int buf_idx = -1;

  if(cache_hit && useful_prefetch){
    stats.num_useful++;
  }

  // Find existing stream buffer for this page
  for (int i = 0; i < (int)NUM_STREAM_BUFFER; i++) {
    if (stream_buffers[i].page == page) {
      buf_idx = i;
      break;
    }
  }
  
  // If not found, allocate new buffer
  if(buf_idx == -1){
    buf_idx = replacement_idx;
    replacement_idx++; // round robin
    if(replacement_idx >= (int)NUM_STREAM_BUFFER){
      replacement_idx = 0;
    }
    stream_buffers[buf_idx].page = page;
    stream_buffers[buf_idx].direction = 0;
    stream_buffers[buf_idx].confidence = 0;
    stream_buffers[buf_idx].pf_idx = (int)page_offset;
    stream_buffers[buf_idx].degree = PREF_DEGREE_DEFAULT;
  }

  // Train new access
  if((int)page_offset > stream_buffers[buf_idx].pf_idx){
    if((page_offset - stream_buffers[buf_idx].pf_idx) < STREAM_WINDOW){
      if(stream_buffers[buf_idx].direction == -1){
        stream_buffers[buf_idx].confidence = 0;
      }else{
        stream_buffers[buf_idx].confidence++;
      }
      stream_buffers[buf_idx].direction = 1;
    }
  }else if((int)page_offset < stream_buffers[buf_idx].pf_idx){
    if((stream_buffers[buf_idx].pf_idx - page_offset) < STREAM_WINDOW){
      if(stream_buffers[buf_idx].direction == 1){
        stream_buffers[buf_idx].confidence = 0;
      }else{
        stream_buffers[buf_idx].confidence++;
      }
      stream_buffers[buf_idx].direction = -1;
    }
  }

  // Do prefetch based on confidence
  if(stream_buffers[buf_idx].confidence >= (int)PREF_CONFIDENCE){
    for(int i = 0; i < (int)PREF_DEGREE_MAX; i++){
      stream_buffers[buf_idx].pf_idx += stream_buffers[buf_idx].direction;
      if((stream_buffers[buf_idx].pf_idx < 0) || (stream_buffers[buf_idx].pf_idx > 64)){
        // out of 4KB page bound
        break;
      }
      uint64_t pf_addr = (stream_buffers[buf_idx].page << LOG2_PAGE_SIZE) + (stream_buffers[buf_idx].pf_idx << LOG2_BLOCK_SIZE);
      
      // Always prefetch to this level
      prefetch_line(champsim::address{pf_addr}, true, metadata_in);
      stats.num_pref++;
    }
  }

  // Adjust degree based on usefulness
  if(useful_prefetch){
    if(stream_buffers[buf_idx].degree < PREF_DEGREE_MAX){
      stream_buffers[buf_idx].degree = stream_buffers[buf_idx].degree * 2;
    }
  }else{
    if(stream_buffers[buf_idx].degree > PREF_DEGREE_DEFAULT){
      stream_buffers[buf_idx].degree = stream_buffers[buf_idx].degree / 2;
    }
  }

  return metadata_in;
}

uint32_t stream::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in)
{
  // TODO: Implement stream prefetcher fill logic here if needed
  return metadata_in;
}

void stream::prefetcher_cycle_operate() {
  // TODO: Implement cycle operation if needed
}

void stream::prefetcher_final_stats() {
  // TODO: Print final statistics if needed
}
