/*
 * [CXLMASK] Known-answer trace generator for the byte-coverage accumulator.
 *
 * The accumulator's whole job is to decide, at the moment a line is written back,
 * whether the stores that ran during its residency covered all 64 bytes.  Checking that
 * against a real workload is circular: the real workload is what we do not yet know.  So
 * this emits lines whose coverage is fixed by construction and prints the expected
 * verdicts, and the simulator's counters either match or the accumulator is wrong.
 *
 * Records are input_instr_sz (72 B) -- run the simulator with --size-trace.
 *
 * Patterns (17.9.5), one line per repetition, each on its own 4 KiB page region so
 * conflicts in the side table do not confound the arithmetic:
 *
 *   full8      8 B x 8, ascending           -> 64/64 covered, full
 *   short8     8 B x 7, last chunk omitted   -> 56/64, partial
 *   full32     32 B x 2                      -> 64/64, full          (no 64 B store exists
 *                                                                     in SPEC17 rate)
 *   stripe8    8 B x 4, every other chunk    -> 32/64, partial and discontiguous
 *   straddle   64 B at offset 32             -> 32/64 on each of two lines, both partial
 *   readgap    8 B x 4 then a load of an unwritten chunk -> partial + read_uncovered
 *
 * The straddle case is the one the model gets wrong today: an access crossing a line
 * boundary is truncated to its first block, which drops the tail entirely (10.0% of
 * 519.lbm_r's stores).  Here both halves are known, so the counter can be checked.
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
  unsigned char destination_size[NUM_INSTR_DESTINATIONS];
  unsigned char source_size[NUM_INSTR_SOURCES];
} input_instr_sz;

#define IP_BASE 0x400000ULL
#define WBUF_BASE 0x200000000ULL /* 8 GiB: far-mapped under only_far */
#define LINE 64ULL

static unsigned long long g_ip = IP_BASE;
static unsigned char g_reg = 10;

static void emit(input_instr_sz* ii)
{
  ii->ip = g_ip;
  g_ip += 4;
  fwrite(ii, sizeof(*ii), 1, stdout);
}

static void emit_store(unsigned long long addr, unsigned char size)
{
  input_instr_sz ii;
  memset(&ii, 0, sizeof(ii));
  ii.source_registers[0] = g_reg;
  ii.destination_memory[0] = addr;
  ii.destination_size[0] = size;
  g_reg = (unsigned char)(10 + ((g_reg - 10 + 1) & 7));
  emit(&ii);
}

static void emit_load(unsigned long long addr, unsigned char size)
{
  input_instr_sz ii;
  memset(&ii, 0, sizeof(ii));
  ii.destination_registers[0] = g_reg;
  ii.source_memory[0] = addr;
  ii.source_size[0] = size;
  g_reg = (unsigned char)(10 + ((g_reg - 10 + 1) & 7));
  emit(&ii);
}

/* Keeps the instruction footprint inside L1I and lets the BTB learn the loop at once. */
static void emit_branch(void)
{
  input_instr_sz br;
  memset(&br, 0, sizeof(br));
  br.is_branch = 1;
  br.branch_taken = 1;
  br.destination_registers[0] = 26; /* REG_INSTRUCTION_POINTER */
  emit(&br);
  g_ip = IP_BASE;
}

enum pattern { P_FULL8, P_SHORT8, P_FULL32, P_STRIPE8, P_STRADDLE, P_READGAP, P_WORD4, P_BYTE3, P_COUNT };

static const char* PATTERN_NAME[P_COUNT] = {"full8", "short8", "full32", "stripe8", "straddle", "readgap", "word4", "byte3"};

/* Lines touched and bytes covered per repetition, for the expectation printed to stderr. */
static const struct {
  int lines;
  int covered_first;
  int covered_second;
  int full_lines;
  int read_uncovered;
} EXPECT[P_COUNT] = {
    {1, 64, 0, 1, 0}, /* full8 */
    {1, 56, 0, 0, 0}, /* short8 */
    {1, 64, 0, 1, 0}, /* full32 */
    {1, 32, 0, 0, 0}, /* stripe8 */
    {2, 32, 32, 0, 0}, /* straddle */
    {1, 32, 0, 0, 1}, /* readgap */
    /* [CXLGATE] word4: 4 B stores at 8 B stride.  Every written 4 B word is complete, so a
       4 B tracker below L1D can hold it -- the crossing gate must pass.  The same mask is
       not 8 B-clean, which is what separates a 4 B from an 8 B tracking unit. */
    {1, 32, 0, 0, 0}, /* word4 */
    /* byte3: one 3 B store leaves word 0 partly written, so it has no representation below
       L1D at all -- the crossing gate must fail on every line. */
    {1, 3, 0, 0, 0}, /* byte3 */
};

static void gen_one(enum pattern p, unsigned long long line_addr)
{
  switch (p) {
  case P_FULL8:
    for (int i = 0; i < 8; ++i) {
      emit_store(line_addr + (unsigned)i * 8, 8);
    }
    break;
  case P_SHORT8:
    for (int i = 0; i < 7; ++i) {
      emit_store(line_addr + (unsigned)i * 8, 8);
    }
    break;
  case P_FULL32:
    emit_store(line_addr, 32);
    emit_store(line_addr + 32, 32);
    break;
  case P_STRIPE8:
    for (int i = 0; i < 8; i += 2) {
      emit_store(line_addr + (unsigned)i * 8, 8);
    }
    break;
  case P_STRADDLE:
    /* Half in this line, half in the next.  Both must record 32 covered bytes. */
    emit_store(line_addr + 32, 64);
    break;
  case P_READGAP:
    for (int i = 0; i < 4; ++i) {
      emit_store(line_addr + (unsigned)i * 8, 8);
    }
    emit_load(line_addr + 40, 8); /* never written by this episode */
    break;
  case P_WORD4:
    /* Complete 4 B words, but only every other one: 4 B-clean, 8 B-dirty. */
    for (int i = 0; i < 8; ++i) {
      emit_store(line_addr + (unsigned)i * 8, 4);
    }
    break;
  case P_BYTE3:
    /* A single sub-word store: word 0 is partly written and nothing below L1D can say so. */
    emit_store(line_addr, 3);
    break;
  case P_COUNT:
  default:
    break;
  }
}

int main(int argc, char** argv)
{
  if (argc < 2) {
    fprintf(stderr, "usage: %s <pattern> [reps]   pattern: full8|short8|full32|stripe8|straddle|readgap|word4|byte3|all\n", argv[0]);
    return 1;
  }
  const unsigned long long reps = (argc > 2) ? strtoull(argv[2], NULL, 10) : 20000;

  int first = 0, last = P_COUNT - 1;
  if (strcmp(argv[1], "all") != 0) {
    int found = -1;
    for (int i = 0; i < P_COUNT; ++i) {
      if (strcmp(argv[1], PATTERN_NAME[i]) == 0) {
        found = i;
      }
    }
    if (found < 0) {
      fprintf(stderr, "unknown pattern '%s'\n", argv[1]);
      return 1;
    }
    first = last = found;
  }

  /* Two lines of stride even for one-line patterns, so a straddling store never spills
     into the next repetition's line and inflates its coverage. */
  unsigned long long line = 0;
  unsigned long long exp_episodes = 0, exp_full = 0, exp_bytes = 0, exp_readunc = 0, exp_straddle = 0;

  for (unsigned long long r = 0; r < reps; ++r) {
    for (int p = first; p <= last; ++p) {
      gen_one((enum pattern)p, WBUF_BASE + line * LINE);
      line += 2;
      exp_episodes += (unsigned)EXPECT[p].lines;
      exp_full += (unsigned)EXPECT[p].full_lines;
      exp_bytes += (unsigned)(EXPECT[p].covered_first + EXPECT[p].covered_second);
      exp_readunc += (unsigned)EXPECT[p].read_uncovered;
      exp_straddle += (p == P_STRADDLE) ? 1 : 0;
    }
    emit_branch();
  }

  /* stderr, so it never lands in the trace itself. */
  fprintf(stderr, "expect episodes_closed %llu  closed_full %llu  covered_bytes_sum %llu  read_uncovered %llu  straddling_stores %llu\n",
          exp_episodes, exp_full, exp_bytes, exp_readunc, exp_straddle);
  fprintf(stderr, "note: holds only if every line is written back exactly once within the measured phase\n");
  return 0;
}
