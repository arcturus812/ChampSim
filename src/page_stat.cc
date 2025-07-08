#include "page_stat.h"
#include "champsim.h"

page_stat_logger::page_stat_logger() {
    l1d_pf_degree.resize(NUM_CPUS, 0);
    l2c_pf_degree.resize(NUM_CPUS, 0);
    llc_pf_degree = 0;
    l1d_prev_pf_addr.resize(NUM_CPUS, 0);
    l2c_prev_pf_addr.resize(NUM_CPUS, 0);
    llc_prev_pf_addr = 0;
}

void page_stat_logger::populate_page_stat_map(uint64_t num_pages, uint64_t base_address){
    fmt::print("[PAGE_STAT] Populating page stat map for {} pages starting from {}\n", num_pages, base_address);
    for (uint64_t i = 0; i < num_pages; i++){
        page_stat_map[base_address + i] = {}; // Initialize with default/empty page_stat
    }
    num_populated_pages = num_pages;
    num_mapped_pages_dram = 0;
    num_mapped_pages_far = 0;
}

bool page_stat_logger::map_pfn_to_vfn(uint64_t pfn, uint64_t vfn, int cpu){
    page_stat* stat = find_page_stat_by_pfn(pfn);
    stat->pfn = pfn;
    stat->vfn = vfn;
    stat->cpu = cpu;
    reverse_map[std::make_pair(vfn, cpu)] = *stat;
    stat->mapped = true;

    // TODO: find page_stat from unmapped_stat_vfn and merge stats
    page_stat* unmapped_stat = find_unmapped_page_stat_by_vfn(vfn, cpu);
    if(unmapped_stat != nullptr){
        merge_page_stat(unmapped_stat, stat);
        free_unmapped_page_stat(unmapped_stat);
    }

    if(is_far_pfn(pfn)){
        num_mapped_pages_far++;
    }else{
        num_mapped_pages_dram++;
    }
    return true;
}


page_stat* page_stat_logger::find_page_stat_by_pfn(uint64_t pfn){
    if(page_stat_map.find(pfn) == page_stat_map.end()){
      return nullptr;
    }
    return &page_stat_map[pfn];
}

page_stat* page_stat_logger::find_unmapped_page_stat_by_vfn(uint64_t vfn, int cpu){
    if(unmapped_stat_vfn.find(std::make_pair(vfn, cpu)) == unmapped_stat_vfn.end()){
        return nullptr;
    }
    return &unmapped_stat_vfn[std::make_pair(vfn, cpu)];
}

page_stat* page_stat_logger::alloc_page_stat(){
    page_stat* stat = new page_stat();
    memset(stat, 0, sizeof(page_stat));

    stat->l1d = new stats();
    stat->l2c = new stats();
    stat->llc = new stats();

    memset(stat->l1d, 0, sizeof(stats));
    memset(stat->l2c, 0, sizeof(stats));
    memset(stat->llc, 0, sizeof(stats));

    return stat;
}

bool page_stat_logger::free_unmapped_page_stat(page_stat* stat){
    if (stat == nullptr){
      return false;
    }
    delete stat->l1d;
    delete stat->l2c;
    delete stat->llc;
    // mapped page should not call this funciton
    if (stat->mapped){
      return false;
    }
    unmapped_stat_vfn.erase(std::make_pair(stat->vfn, stat->cpu));
    return true;
}

void page_stat_logger::merge_page_stat(page_stat* src, page_stat* dst)
{
  // merge src into dst
  // l1d
  dst->l1d->hit += src->l1d->hit;
  dst->l1d->miss += src->l1d->miss;
  dst->l1d->prefetch += src->l1d->prefetch;
  dst->l1d->useful_prefetch_hit += src->l1d->useful_prefetch_hit;
  dst->l1d->pf_degree_sum += src->l1d->pf_degree_sum;
  dst->l1d->pf_degree_cnt += src->l1d->pf_degree_cnt;
  dst->l1d->useless_prefetch += src->l1d->useless_prefetch;

  // l2c
  dst->l2c->hit += src->l2c->hit;
  dst->l2c->miss += src->l2c->miss;
  dst->l2c->prefetch += src->l2c->prefetch;
  dst->l2c->useful_prefetch_hit += src->l2c->useful_prefetch_hit;
  dst->l2c->pf_degree_sum += src->l2c->pf_degree_sum;
  dst->l2c->pf_degree_cnt += src->l2c->pf_degree_cnt;
  dst->l2c->useless_prefetch += src->l2c->useless_prefetch;

  // llc
  dst->llc->hit += src->llc->hit;
  dst->llc->miss += src->llc->miss;
  dst->llc->prefetch += src->llc->prefetch;
  dst->llc->useful_prefetch_hit += src->llc->useful_prefetch_hit;
  dst->llc->pf_degree_sum += src->llc->pf_degree_sum;
  dst->llc->pf_degree_cnt += src->llc->pf_degree_cnt;
  dst->llc->useless_prefetch += src->llc->useless_prefetch;
}

bool page_stat_logger::event_log(std::string caller, PAGE_STAT_EVENT event, uint64_t pfn, uint64_t vfn, int cpu, uint64_t value)
{
  (void)value; // Suppress unused parameter warning - will be used in future
  PAGE_STAT_CALLER caller_type;
  if (caller.find("L1D") != std::string::npos) {
    caller_type = PAGE_STAT_CALLER::L1D;
  }else if(caller.find("L2C") != std::string::npos){
    caller_type = PAGE_STAT_CALLER::L2C;
  }else if(caller.find("LLC") != std::string::npos){
    caller_type = PAGE_STAT_CALLER::LLC;
  }else{
    fmt::print("[PAGE_STAT] WARNING: not registered caller: {}\n", caller);
    return false;
  }
  int event_type = caller_type + event;
  page_stat* stat = nullptr;
  stat = find_unmapped_page_stat_by_vfn(vfn, cpu);
  if (stat == nullptr) {
    stat = find_page_stat_by_pfn(pfn);
  }
  if (stat == nullptr) {
    stat = alloc_page_stat();
    stat->vfn = vfn;
    stat->cpu = cpu;
    unmapped_stat_vfn[std::make_pair(vfn, cpu)] = *stat;
  }
  if(stat->l1d == nullptr || stat->l2c == nullptr || stat->llc == nullptr){
    stat->pfn = pfn;
    stat->l1d = new stats();
    stat->l2c = new stats();
    stat->llc = new stats();
    memset(stat->l1d, 0, sizeof(stats));
    memset(stat->l2c, 0, sizeof(stats));
    memset(stat->llc, 0, sizeof(stats));
  }

  switch (event_type) {
  case PAGE_STAT_CALLER::L1D + PAGE_STAT_EVENT::HIT:
    stat->l1d->hit++;
    break;
  case PAGE_STAT_CALLER::L1D + PAGE_STAT_EVENT::MISS:
    stat->l1d->miss++;
    break;
  case PAGE_STAT_CALLER::L2C + PAGE_STAT_EVENT::HIT:
    stat->l2c->hit++;
    break;
  case PAGE_STAT_CALLER::L2C + PAGE_STAT_EVENT::MISS:
    stat->l2c->miss++;
    break;
  case PAGE_STAT_CALLER::LLC + PAGE_STAT_EVENT::HIT:
    stat->llc->hit++;
    break;
  case PAGE_STAT_CALLER::LLC + PAGE_STAT_EVENT::MISS:
    stat->llc->miss++;
    break;
  case PAGE_STAT_CALLER::L1D + PAGE_STAT_EVENT::USEFUL_PREFETCH:
    stat->l1d->hit++;
    stat->l1d->useful_prefetch_hit++;
    break;
  case PAGE_STAT_CALLER::L2C + PAGE_STAT_EVENT::USEFUL_PREFETCH:
    stat->l2c->hit++;
    stat->l2c->useful_prefetch_hit++;
    break;
  case PAGE_STAT_CALLER::LLC + PAGE_STAT_EVENT::USEFUL_PREFETCH:
    stat->llc->hit++;
    stat->llc->useful_prefetch_hit++;
    break;
  case PAGE_STAT_CALLER::L1D + PAGE_STAT_EVENT::PREFETCH:
    if(l1d_prev_pf_addr[cpu] == pfn){
        l1d_pf_degree[cpu]++;
    }else{
      page_stat* prev_stat = find_page_stat_by_pfn(l1d_prev_pf_addr[cpu]);
      if(prev_stat != nullptr){
        prev_stat->l1d->pf_degree_sum += l1d_pf_degree[cpu];
        prev_stat->l1d->pf_degree_cnt++;
      }else{
        fmt::print("[PAGE_STAT] WARNING: prev stat is nullptr. if this is the first prefetch, it's normal\n");
      }
      l1d_pf_degree[cpu] = 0;
      l1d_prev_pf_addr[cpu] = pfn;
    }
    break;
  case PAGE_STAT_CALLER::L2C + PAGE_STAT_EVENT::PREFETCH:
    if(l2c_prev_pf_addr[cpu] == pfn){
        l2c_pf_degree[cpu]++;
    }else{
      page_stat* prev_stat = find_page_stat_by_pfn(l2c_prev_pf_addr[cpu]);
      if(prev_stat != nullptr){
        prev_stat->l2c->pf_degree_sum += l2c_pf_degree[cpu];
        prev_stat->l2c->pf_degree_cnt++;
      }else{
        fmt::print("[PAGE_STAT] WARNING: prev stat is nullptr. if this is the first prefetch, it's normal\n");
      }
      l2c_pf_degree[cpu] = 0;
      l2c_prev_pf_addr[cpu] = pfn;
    }
    break;
  case PAGE_STAT_CALLER::LLC + PAGE_STAT_EVENT::PREFETCH:
    if(llc_prev_pf_addr == pfn){
        llc_pf_degree++;
    }else{
        page_stat* prev_stat = find_page_stat_by_pfn(llc_prev_pf_addr);
        if(prev_stat != nullptr){
            prev_stat->llc->pf_degree_sum += llc_pf_degree;
            prev_stat->llc->pf_degree_cnt++;
        }else{
            fmt::print("[PAGE_STAT] WARNING: prev stat is nullptr. if this is the first prefetch, it's normal\n");
        }
        llc_pf_degree = 0;
        llc_prev_pf_addr = pfn;
    }
    break;
  case PAGE_STAT_CALLER::L1D + PAGE_STAT_EVENT::USELESS_PREFETCH:
    stat->l1d->useless_prefetch++;
    break;
  case PAGE_STAT_CALLER::L2C + PAGE_STAT_EVENT::USELESS_PREFETCH:
    stat->l2c->useless_prefetch++;
    break;
  case PAGE_STAT_CALLER::LLC + PAGE_STAT_EVENT::USELESS_PREFETCH:
    stat->llc->useless_prefetch++;
    break;
  case PAGE_STAT_CALLER::L1D + PAGE_STAT_EVENT::MSHR_PREFETCH_HIT:
    stat->l1d->mshr_prefetch_hit++;
    break;
  case PAGE_STAT_CALLER::L2C + PAGE_STAT_EVENT::MSHR_PREFETCH_HIT:
    stat->l2c->mshr_prefetch_hit++;
    break;
  case PAGE_STAT_CALLER::LLC + PAGE_STAT_EVENT::MSHR_PREFETCH_HIT:
    stat->llc->mshr_prefetch_hit++;
    break;
  default:
    fmt::print("[PAGE_STAT] WARNING: unknown event: {} {}\n", caller, static_cast<int>(event));
    return false;
  }
  return true;
}

void page_stat_logger::print_page_stat_map_to_csv(){
    fmt::print("[START_PAGE_STAT] Printing page stat map to csv format\n");
    fmt::print("pfn,vfn,cpu,l1d_hit,l1d_miss,l1d_prefetch,l1d_useful_prefetch_hit,l1d_pf_degree_sum,l1d_pf_degree_cnt,l1d_useless_prefetch,l1d_mshr_prefetch_hit,l2c_hit,l2c_miss,l2c_prefetch,l2c_useful_prefetch_hit,l2c_pf_degree_sum,l2c_pf_degree_cnt,l2c_useless_prefetch,l2c_mshr_prefetch_hit,llc_hit,llc_miss,llc_prefetch,llc_useful_prefetch_hit,llc_pf_degree_sum,llc_pf_degree_cnt,llc_useless_prefetch,llc_mshr_prefetch_hit\n");
    for (auto &[pfn, page_stat] : page_stat_map){
        if(page_stat.mapped){
            fmt::print("{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}\n",
            pfn, 
            page_stat.vfn,
            page_stat.cpu,
            page_stat.l1d->hit,
            page_stat.l1d->miss,
            page_stat.l1d->prefetch,
            page_stat.l1d->useful_prefetch_hit,
            page_stat.l1d->mshr_prefetch_hit,
            page_stat.l1d->pf_degree_sum,
            page_stat.l1d->pf_degree_cnt,
            page_stat.l1d->useless_prefetch,
            page_stat.l1d->mshr_prefetch_hit,
            page_stat.l2c->hit,
            page_stat.l2c->miss,
            page_stat.l2c->prefetch,
            page_stat.l2c->useful_prefetch_hit,
            page_stat.l2c->mshr_prefetch_hit,
            page_stat.l2c->pf_degree_sum,
            page_stat.l2c->pf_degree_cnt,
            page_stat.l2c->useless_prefetch,
            page_stat.l2c->mshr_prefetch_hit,
            page_stat.llc->hit,
            page_stat.llc->miss,
            page_stat.llc->prefetch,
            page_stat.llc->useful_prefetch_hit,
            page_stat.llc->mshr_prefetch_hit,
            page_stat.llc->pf_degree_sum,
            page_stat.llc->pf_degree_cnt,
            page_stat.llc->useless_prefetch,
            page_stat.llc->mshr_prefetch_hit);
        }
    }
    fmt::print("[END_PAGE_STAT] mapped_pages_dram: {}, mapped_pages_far: {}\n", num_mapped_pages_dram, num_mapped_pages_far);
}
