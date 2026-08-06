/*
 * [CXLELIDE] STREAM-family trace generator: the analytic anchors for write-allocate.
 *
 * Write-allocate fetch cannot exceed 50% of memory traffic. A written line costs
 * one fetch plus one writeback, so per useful line the traffic is fixed by the
 * kernel's load:store ratio alone:
 *
 *   kernel  loads:stores  lines/useful line   WA share   elide's ceiling
 *   fill        0:1            2 / 1 = 2.000    50.0%        +100%
 *   copy        1:1            3 / 2 = 1.500    33.3%         +50%
 *   scale       1:1            3 / 2 = 1.500    33.3%         +50%
 *   add         2:1            4 / 3 = 1.333    25.0%         +33%
 *   triad       2:1            4 / 3 = 1.333    25.0%         +33%
 *   rmw         2:1            3 / 3 = 1.000     0.0%           0%   <- control
 *
 * These are arithmetic, not estimates, which is what makes them useful: if the
 * model reproduces +100 / +50 / +33 / 0 percent, the mechanism is verified against
 * ground truth rather than against itself. Measured 619.lbm sits at 42.8% WA and
 * +77%, i.e. between copy and fill, near the physical ceiling.
 *
 * rmw is the deliberate negative control. It moves exactly as many bytes as add,
 * but its store targets the line its own load just brought in, so no write-allocate
 * fetch is generated at all. This is the pattern that gave 649.fotonik3d and GAP cc
 * a measured 0% WA despite heavy writing; reproducing it on demand shows the
 * mechanism is selective rather than merely beneficial.
 *
 * One array element is a full 64 B line, so every access is a distinct line and
 * every store covers a whole line -- the case the paper's premise is about.
 *
 * Usage: streamgen <kernel> <buf_mib> > trace.champsim
 *   kernel    fill | copy | scale | add | triad | rmw
 *   buf_mib   size of EACH array in MiB (use >= 8 to clear the modelled 2 MiB LLC)
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
#define A_BASE 0x100000000ULL /*  4 GiB */
#define B_BASE 0x200000000ULL /*  8 GiB */
#define C_BASE 0x300000000ULL /* 12 GiB */
#define LINE 64ULL
#define ELEMS_PER_BODY 8 /* elements between loop-closing branches */

static void emit(input_instr* ii) { fwrite(ii, sizeof(*ii), 1, stdout); }

enum kernel { K_FILL, K_COPY, K_SCALE, K_ADD, K_TRIAD, K_RMW };

int main(int argc, char** argv)
{
  if (argc < 3) {
    fprintf(stderr, "usage: %s <fill|copy|scale|add|triad|rmw> <buf_mib>\n", argv[0]);
    return 1;
  }
  enum kernel k;
  if (!strcmp(argv[1], "fill"))
    k = K_FILL;
  else if (!strcmp(argv[1], "copy"))
    k = K_COPY;
  else if (!strcmp(argv[1], "scale"))
    k = K_SCALE;
  else if (!strcmp(argv[1], "add"))
    k = K_ADD;
  else if (!strcmp(argv[1], "triad"))
    k = K_TRIAD;
  else if (!strcmp(argv[1], "rmw"))
    k = K_RMW;
  else {
    fprintf(stderr, "unknown kernel '%s'\n", argv[1]);
    return 1;
  }

  const unsigned long long buf_mib = (unsigned long long)atoi(argv[2]);
  const unsigned long long nlines = buf_mib * 1024ULL * 1024ULL / LINE;
  if (nlines == 0) {
    fprintf(stderr, "buf_mib too small\n");
    return 1;
  }

  unsigned char reg = 10; /* 10..17 rotate so consecutive elements stay independent */

  for (unsigned long long base = 0; base < nlines; base += ELEMS_PER_BODY) {
    unsigned long long ip = IP_BASE;

    for (unsigned e = 0; e < ELEMS_PER_BODY; ++e) {
      const unsigned long long i = (base + e) % nlines;
      const unsigned long long a = A_BASE + i * LINE;
      const unsigned long long b = B_BASE + i * LINE;
      const unsigned long long c = C_BASE + i * LINE;
      const unsigned char r0 = reg;
      const unsigned char r1 = (unsigned char)(10 + ((reg - 10 + 1) & 7));

      if (k == K_RMW) {
        /* a[i] += b[i] as one read-modify-write instruction: the store's line is
           the line this very instruction loads, so it can never miss for want of
           data and no write-allocate fetch is issued. */
        input_instr ii;
        memset(&ii, 0, sizeof(ii));
        ii.ip = ip;
        ip += 4;
        ii.source_memory[0] = a;
        ii.source_memory[1] = b;
        ii.destination_memory[0] = a;
        ii.source_registers[0] = r0;
        ii.destination_registers[0] = r0;
        emit(&ii);
      } else {
        /* loads first, then the store that consumes them */
        unsigned long long ld[2];
        unsigned nld = 0;
        switch (k) {
        case K_FILL:
          break;
        case K_COPY:
          ld[nld++] = a;
          break;
        case K_SCALE:
          ld[nld++] = c;
          break;
        case K_ADD:
          ld[nld++] = a;
          ld[nld++] = b;
          break;
        case K_TRIAD:
          ld[nld++] = b;
          ld[nld++] = c;
          break;
        default:
          break;
        }
        for (unsigned j = 0; j < nld; ++j) {
          input_instr ii;
          memset(&ii, 0, sizeof(ii));
          ii.ip = ip;
          ip += 4;
          ii.destination_registers[0] = (j == 0) ? r0 : r1;
          ii.source_memory[0] = ld[j];
          emit(&ii);
        }
        input_instr st;
        memset(&st, 0, sizeof(st));
        st.ip = ip;
        ip += 4;
        st.source_registers[0] = r0;
        if (nld > 1) {
          st.source_registers[1] = r1;
        }
        /* fill/copy/scale/add write c[]; triad writes a[] (STREAM's own naming) */
        st.destination_memory[0] = (k == K_TRIAD) ? a : c;
        emit(&st);
      }

      reg = (unsigned char)(10 + ((reg - 10 + 2) & 7));
    }

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
