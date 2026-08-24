/*
 *    Copyright 2023 The ChampSim Contributors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef TRACE_INSTRUCTION_H
#define TRACE_INSTRUCTION_H

#include <limits>

// special registers that help us identify branches
namespace champsim
{
constexpr char REG_STACK_POINTER = 6;
constexpr char REG_FLAGS = 25;
constexpr char REG_INSTRUCTION_POINTER = 26;
} // namespace champsim

// instruction format
constexpr std::size_t NUM_INSTR_DESTINATIONS_SPARC = 4;
constexpr std::size_t NUM_INSTR_DESTINATIONS = 2;
constexpr std::size_t NUM_INSTR_SOURCES = 4;

// NOLINTBEGIN(cppcoreguidelines-avoid-c-arrays,modernize-avoid-c-arrays): These classes are deliberately trivial
struct input_instr {
  // instruction pointer or PC (Program Counter)
  unsigned long long ip;

  // branch info
  unsigned char is_branch;
  unsigned char branch_taken;

  unsigned char destination_registers[NUM_INSTR_DESTINATIONS]; // output registers
  unsigned char source_registers[NUM_INSTR_SOURCES];           // input registers

  unsigned long long destination_memory[NUM_INSTR_DESTINATIONS]; // output memory
  unsigned long long source_memory[NUM_INSTR_SOURCES];           // input memory
};

// [CXLSIZE] The stock record carries addresses but not extents, so a store that covers a
// whole line is indistinguishable from one that covers eight bytes of it.  That is the gap
// that makes write-allocate elision unmeasurable: whether eliding a fetch was safe depends
// on which bytes of the line were eventually written, and the trace does not say.
//
// This variant adds one byte per memory operand, in the same slot order as the address
// arrays.  Encoding:
//
//   0        no operand (mirrors a zero address in the matching slot)
//   1..64    contiguous bytes beginning at the recorded address
//   254      larger than a cache line -- the XSAVE family; the consumer splits it
//   255      irregular: an AVX-512 masked store, or a scatter with no single extent.
//            Recorded as its own value rather than as 64, because calling a masked store
//            full-line would make a partial write look safe and would loosen the very
//            bound this field exists to tighten.  Consumers must treat it as partial.
//
// Selected by a command-line flag, following the cloudsuite convention: the format has no
// header or magic number, so the record type cannot be detected from the file.
constexpr unsigned char ACCESS_SIZE_NONE = 0;
constexpr unsigned char ACCESS_SIZE_OVERSIZE = 254;
constexpr unsigned char ACCESS_SIZE_IRREGULAR = 255;

struct input_instr_sz {
  // instruction pointer or PC (Program Counter)
  unsigned long long ip;

  // branch info
  unsigned char is_branch;
  unsigned char branch_taken;

  unsigned char destination_registers[NUM_INSTR_DESTINATIONS]; // output registers
  unsigned char source_registers[NUM_INSTR_SOURCES];           // input registers

  unsigned long long destination_memory[NUM_INSTR_DESTINATIONS]; // output memory
  unsigned long long source_memory[NUM_INSTR_SOURCES];           // input memory

  unsigned char destination_size[NUM_INSTR_DESTINATIONS]; // bytes written per slot
  unsigned char source_size[NUM_INSTR_SOURCES];           // bytes read per slot
};

struct cloudsuite_instr {
  // instruction pointer or PC (Program Counter)
  unsigned long long ip;

  // branch info
  unsigned char is_branch;
  unsigned char branch_taken;

  unsigned char destination_registers[NUM_INSTR_DESTINATIONS_SPARC]; // output registers
  unsigned char source_registers[NUM_INSTR_SOURCES];                 // input registers

  unsigned long long destination_memory[NUM_INSTR_DESTINATIONS_SPARC]; // output memory
  unsigned long long source_memory[NUM_INSTR_SOURCES];                 // input memory

  unsigned char asid[2];
};
// NOLINTEND(cppcoreguidelines-avoid-c-arrays,modernize-avoid-c-arrays)

#endif
