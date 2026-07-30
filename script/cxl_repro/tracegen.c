/*
 * [CXLREPRO] ChampSim trace generator for the CXL write-allocate reproduction.
 *
 * Per 16-line period, R lines are loaded from the read buffer and (16-R)
 * lines are stored to the write buffer, giving an exact read fraction
 * rf = R/16 of useful line traffic at every point in the trace. Both streams
 * advance sequentially and wrap independently, so the mix never drifts.
 * ChampSim repeats the trace file automatically at EOF.
 *
 * Usage: tracegen <R_out_of_16> <buf_mib> [reuse_dist_lines] > trace.champsim
 *   R_out_of_16      0..16, read lines per 16-line period
 *   buf_mib          size of EACH buffer (read and write) in MiB
 *   reuse_dist_lines (optional, default 0 = streaming, no reuse)
 *                    if >0: every stored line is loaded again after this many
 *                    further lines have been stored (write -> re-read)
 *                    if -1 ("clash" mode): every line is loaded and then
 *                    immediately stored by the next instruction, so the store
 *                    always finds the load's MSHR in flight (corner case for
 *                    the NT-bypass MSHR guard); R is ignored
 *
 * Instruction stream shape: bodies of 16 memory instructions followed by one
 * unconditional taken branch back to the loop head, so the I-footprint stays
 * inside L1I and the BTB learns the loop immediately.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NUM_INSTR_DESTINATIONS 2
#define NUM_INSTR_SOURCES 4

typedef struct {
  unsigned long long ip;
  unsigned char is_branch;
  unsigned char branch_taken;
  unsigned char destination_registers[NUM_INSTR_DESTINATIONS];
  unsigned char source_registers[NUM_INSTR_SOURCES];
  unsigned long long destination_memory[NUM_INSTR_DESTINATIONS];
  unsigned long long source_memory[NUM_INSTR_SOURCES];
} input_instr;

#define IP_BASE 0x400000ULL
#define RBUF_BASE 0x100000000ULL /* 4 GiB, page-aligned, far-mapped under only_far */
#define WBUF_BASE 0x200000000ULL /* 8 GiB */
#define LINE 64ULL
#define PERIOD 16

static void emit(input_instr* ii) { fwrite(ii, sizeof(*ii), 1, stdout); }

int main(int argc, char** argv)
{
  if (argc < 3) {
    fprintf(stderr, "usage: %s <R_out_of_16> <buf_mib> [reuse_dist_lines]\n", argv[0]);
    return 1;
  }
  const unsigned R = (unsigned)atoi(argv[1]);
  const unsigned long long buf_mib = (unsigned long long)atoi(argv[2]);
  const long long reuse_arg = (argc > 3) ? atoll(argv[3]) : 0;
  const int clash_mode = (reuse_arg < 0);
  const unsigned long long reuse_dist = clash_mode ? 0 : (unsigned long long)reuse_arg;
  if (R > PERIOD) {
    fprintf(stderr, "R must be 0..16\n");
    return 1;
  }

  const unsigned long long nlines = buf_mib * 1024ULL * 1024ULL / LINE;

  if (clash_mode) {
    /* load line L, then store line L with the very next instruction */
    unsigned char creg = 10;
    for (unsigned long long l = 0; l < nlines; ++l) {
      unsigned long long ip = IP_BASE;
      input_instr ld, st, br;
      memset(&ld, 0, sizeof(ld));
      ld.ip = ip;
      ld.destination_registers[0] = creg;
      ld.source_memory[0] = WBUF_BASE + l * LINE;
      emit(&ld);
      memset(&st, 0, sizeof(st));
      st.ip = ip + 4;
      st.source_registers[0] = creg;
      st.destination_memory[0] = WBUF_BASE + l * LINE;
      emit(&st);
      memset(&br, 0, sizeof(br));
      br.ip = ip + 8;
      br.is_branch = 1;
      br.branch_taken = 1;
      br.destination_registers[0] = 26;
      emit(&br);
      creg = (unsigned char)(10 + ((creg - 10 + 1) & 7));
    }
    return 0;
  }
  /* one full pass over the larger of the two consumed streams; the trace is
     repeated by the simulator, so absolute length only needs to cover the
     buffer footprint once to defeat cache reuse */
  const unsigned long long total_periods = 2 * nlines / PERIOD;

  unsigned long long rline = 0, wline = 0;
  unsigned char reg = 10; /* registers 10..17 rotate as load dests / store sources */

  for (unsigned long long p = 0; p < total_periods; ++p) {
    unsigned long long ip = IP_BASE;
    for (unsigned i = 0; i < PERIOD; ++i) {
      input_instr ii;
      memset(&ii, 0, sizeof(ii));
      ii.ip = ip;
      ip += 4;

      if (i < R) {
        ii.destination_registers[0] = reg;
        ii.source_memory[0] = RBUF_BASE + (rline % nlines) * LINE;
        ++rline;
      } else {
        ii.source_registers[0] = reg;
        ii.destination_memory[0] = WBUF_BASE + (wline % nlines) * LINE;
        ++wline;
        if (reuse_dist > 0 && wline > reuse_dist) {
          /* re-read the line stored reuse_dist lines ago (extra source op) */
          ii.destination_registers[1] = (unsigned char)(reg + 8);
          ii.source_memory[1] = WBUF_BASE + ((wline - 1 - reuse_dist) % nlines) * LINE;
        }
      }
      reg = (unsigned char)(10 + ((reg - 10 + 1) & 7));
      emit(&ii);
    }
    /* loop-closing unconditional taken branch (writes IP only) */
    input_instr br;
    memset(&br, 0, sizeof(br));
    br.ip = ip;
    br.is_branch = 1;
    br.branch_taken = 1;
    br.destination_registers[0] = 26; /* REG_INSTRUCTION_POINTER */
    emit(&br);
  }
  return 0;
}
