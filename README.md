# ChampSim

![GitHub](https://img.shields.io/github/license/ChampSim/ChampSim)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/ChampSim/ChampSim/test.yml)
![GitHub forks](https://img.shields.io/github/forks/ChampSim/ChampSim)
[![Coverage Status](https://coveralls.io/repos/github/ChampSim/ChampSim/badge.svg?branch=develop)](https://coveralls.io/github/ChampSim/ChampSim?branch=develop)

ChampSim is a trace-based simulator for a microarchitecture study. If you have questions about how to use ChampSim, we encourage you to search the threads in the Discussions tab or start your own thread. If you are aware of a bug or have a feature request, open a new Issue.

# Using ChampSim

ChampSim is the result of academic research. If you use this software in your work, please cite it using the following reference:

    Gober, N., Chacon, G., Wang, L., Gratz, P. V., Jimenez, D. A., Teran, E., Pugsley, S., & Kim, J. (2022). The Championship Simulator: Architectural Simulation for Education and Competition. https://doi.org/10.48550/arXiv.2210.14324

If you use ChampSim in your work, you may submit a pull request modifying `PUBLICATIONS_USING_CHAMPSIM.bib` to have it featured in [the documentation](https://champsim.github.io/ChampSim/master/Publications-using-champsim.html).

# Download dependencies

ChampSim uses [vcpkg](https://vcpkg.io) to manage its dependencies. In this repository, vcpkg is included as a submodule. You can download the dependencies with
```
git submodule update --init
vcpkg/bootstrap-vcpkg.sh
vcpkg/vcpkg install
```

# Compile

ChampSim takes a JSON configuration script. Examine `champsim_config.json` for a fully-specified example. All options described in this file are optional and will be replaced with defaults if not specified. The configuration scrip can also be run without input, in which case an empty file is assumed.
```
$ ./config.sh <configuration file>
$ make
```

# Tiered Memory Architecture (TMA) Feature

ChampSim supports Tiered Memory Architecture (TMA) with DRAM and far memory tiers. This feature allows simulation of heterogeneous memory systems with different latency and capacity characteristics.

## Configuration

### 1. Enable TMA in champsim_config.json

Set the `TMA` flag to `true` in your configuration file:

```json
{
  "TMA": true,
  "physical_memory": {
    "data_rate": 3200,
    "channels": 1,
    "ranks": 1,
    "bankgroups": 8,
    "banks": 4,
    "bank_rows": 65536,
    "bank_columns": 1024,
    "channel_width": 8,
    "wq_size": 64,
    "rq_size": 64,
    "tCAS": 24,
    "tRCD": 24,
    "tRP": 24,
    "tRAS": 52,
    "refresh_period": 32,
    "refreshes_per_period": 8192,
    "tADD": 0
  },
  "physical_memory_far": {
    "data_rate": 3200,
    "channels": 1,
    "ranks": 4,
    "bankgroups": 8,
    "banks": 4,
    "bank_rows": 65536,
    "bank_columns": 1024,
    "channel_width": 8,
    "wq_size": 64,
    "rq_size": 64,
    "tCAS": 24,
    "tRCD": 24,
    "tRP": 24,
    "tRAS": 52,
    "refresh_period": 32,
    "refreshes_per_period": 8192,
    "tADD": 72
  }
}
```

### 2. Memory Allocation Policies

ChampSim supports four different memory allocation policies for TMA:

#### Policy Configuration in vmem.h

The memory allocation policy can be configured by modifying the `allocation_policy` variable in `inc/vmem.h`:

```cpp
// Memory allocation policy selection
MemoryAllocationPolicy allocation_policy = MemoryAllocationPolicy::ROUND_ROBIN;
```

#### Available Policies

1. **FIRST_TOUCH**: Allocates pages to DRAM on first access, switches to far memory only when DRAM is full
   ```cpp
   MemoryAllocationPolicy allocation_policy = MemoryAllocationPolicy::FIRST_TOUCH;
   ```

2. **ONLY_FAR_MEM**: Always allocates pages to far memory
   ```cpp
   MemoryAllocationPolicy allocation_policy = MemoryAllocationPolicy::ONLY_FAR_MEM;
   ```

3. **ROUND_ROBIN**: Alternates between DRAM and far memory for page allocations
   ```cpp
   MemoryAllocationPolicy allocation_policy = MemoryAllocationPolicy::ROUND_ROBIN;
   ```

4. **FEEDBACK**: Uses feedback-based allocation (currently placeholder implementation)
   ```cpp
   MemoryAllocationPolicy allocation_policy = MemoryAllocationPolicy::FEEDBACK;
   ```

### 3. Policy Implementation Details

Each policy is implemented as a separate function in `src/vmem.cc`:

- `should_allocate_to_far_memory_first_touch()`: DRAM-first allocation
- `should_allocate_to_far_memory_only_far_mem()`: Far memory only
- `should_allocate_to_far_memory_round_robin()`: Alternating allocation
- `should_allocate_to_far_memory_feedback()`: Feedback-based (TODO)

### 4. Running TMA Simulations

To run simulations with TMA enabled:

```bash
# Configure with TMA enabled
./config.sh champsim_config.json

# Build
make

# Run simulation
./bin/champsim --warmup-instructions 200000000 --simulation-instructions 500000000 trace_file.champsimtrace.xz
```

### 5. Debug Output

When debug printing is enabled, the virtual memory system will output allocation decisions:

```
[VMEM] va_to_pa paddr: 0x1000 vpage: 0x1000 fault: 1 alloc_far: 0
```

This shows whether each page allocation went to DRAM (`alloc_far: 0`) or far memory (`alloc_far: 1`).

## Memory Characteristics

- **DRAM**: Lower latency, smaller capacity
- **Far Memory**: Higher latency (additional `tADD` penalty), larger capacity

The far memory configuration includes an additional `tADD` parameter (72 cycles in the example) that represents the additional latency for accessing far memory.

# Download DPC-3 trace

Traces used for the 3rd Data Prefetching Championship (DPC-3) can be found here. (https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu/) A set of traces used for the 2nd Cache Replacement Championship (CRC-2) can be found from this link. (http://bit.ly/2t2nkUj)

Storage for these traces is kindly provided by Daniel Jimenez (Texas A&M University) and Mike Ferdman (Stony Brook University). If you find yourself frequently using ChampSim, it is highly encouraged that you maintain your own repository of traces, in case the links ever break.

# Run simulation

Execute the binary directly.
```
$ bin/champsim --warmup-instructions 200000000 --simulation-instructions 500000000 ~/path/to/traces/600.perlbench_s-210B.champsimtrace.xz
```

The number of warmup and simulation instructions given will be the number of instructions retired. Note that the statistics printed at the end of the simulation include only the simulation phase.

# Add your own branch predictor, data prefetchers, and replacement policy
**Copy an empty template**
```
$ mkdir prefetcher/mypref
$ cp prefetcher/no_l2c/no.cc prefetcher/mypref/mypref.cc
```

**Work on your algorithms with your favorite text editor**
```
$ vim prefetcher/mypref/mypref.cc
```

**Compile and test**
Add your prefetcher to the configuration file.
```
{
    "L2C": {
        "prefetcher": "mypref"
    }
}
```
Note that the example prefetcher is an L2 prefetcher. You might design a prefetcher for a different level.

```
$ ./config.sh <configuration file>
$ make
$ bin/champsim --warmup-instructions 200000000 --simulation-instructions 500000000 600.perlbench_s-210B.champsimtrace.xz
```

# How to create traces

Program traces are available in a variety of locations, however, many ChampSim users wish to trace their own programs for research purposes.
Example tracing utilities are provided in the `tracer/` directory.

# Evaluate Simulation

ChampSim measures the IPC (Instruction Per Cycle) value as a performance metric. <br>
There are some other useful metrics printed out at the end of simulation. <br>

Good luck and be a champion! <br>
