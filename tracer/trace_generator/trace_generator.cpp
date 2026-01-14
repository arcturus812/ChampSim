/*
 * MLP Trace Generator for ChampSim
 * 
 * Generates trace files with independent load instructions to test
 * Memory Level Parallelism (MLP) in ChampSim simulator.
 */

#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <string>
#include <cstdlib>

#include "../../inc/trace_instruction.h"

using namespace std;

// Constants
constexpr uint64_t BASE_PC = 0x400000;
constexpr uint64_t BASE_ADDR = 0x10000000;
constexpr uint64_t CACHE_LINE_SIZE = 64;
constexpr uint8_t MAX_REGS = 31;  // R1 to R31 (avoid R0)

enum class PatternType {
    INDEPENDENT,
    SEQUENTIAL,
    STRIDE,
    RANDOM,
    MIXED
};

struct Config {
    PatternType pattern = PatternType::INDEPENDENT;
    uint64_t num_instructions = 100000;
    uint64_t stride = 64;
    uint64_t base_addr = BASE_ADDR;
    string output_file = "output.trace";
    uint64_t window_size = 10;  // For independent pattern
};

// Generate a single load instruction
input_instr generate_load_instruction(uint64_t pc, uint64_t memory_addr, uint8_t dest_reg) {
    input_instr instr = {};
    
    instr.ip = pc;
    instr.is_branch = 0;
    instr.branch_taken = 0;
    
    // Load: source_memory[0]에 주소 설정
    instr.source_memory[0] = memory_addr;
    instr.source_memory[1] = 0;
    instr.source_memory[2] = 0;
    instr.source_memory[3] = 0;
    
    // Destination register (의존성 회피를 위해 다른 레지스터 사용)
    instr.destination_registers[0] = dest_reg;
    instr.destination_registers[1] = 0;
    
    // Source registers는 사용 안함 (pure load, 의존성 없음)
    memset(instr.source_registers, 0, sizeof(instr.source_registers));
    
    // Destination memory는 사용 안함 (load이므로)
    memset(instr.destination_memory, 0, sizeof(instr.destination_memory));
    
    return instr;
}

// Write instruction to trace file
void write_instruction(ofstream& outfile, const input_instr& instr) {
    outfile.write(reinterpret_cast<const char*>(&instr), sizeof(input_instr));
}

// Pattern 1: Independent Loads (High MLP)
void generate_independent_pattern(ofstream& outfile, const Config& config) {
    cout << "Generating independent load pattern (High MLP)..." << endl;
    cout << "Window size: " << config.window_size << endl;
    
    for (uint64_t i = 0; i < config.num_instructions; i++) {
        uint64_t pc = BASE_PC + i * 4;
        // 각 load는 서로 다른 캐시 라인에 접근 (64B 간격)
        uint64_t addr = config.base_addr + (i * CACHE_LINE_SIZE);
        // 다른 레지스터 사용 (R1~R31 순환, 0은 제외)
        uint8_t reg = (i % MAX_REGS) + 1;
        
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
}

// Pattern 2: Sequential Loads
void generate_sequential_pattern(ofstream& outfile, const Config& config) {
    cout << "Generating sequential load pattern..." << endl;
    
    for (uint64_t i = 0; i < config.num_instructions; i++) {
        uint64_t pc = BASE_PC + i * 4;
        // 연속된 주소 접근 (8 bytes stride)
        uint64_t addr = config.base_addr + (i * 8);
        // 다른 레지스터 사용 (의존성 없음)
        uint8_t reg = (i % MAX_REGS) + 1;
        
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
}

// Pattern 3: Stride Loads
void generate_stride_pattern(ofstream& outfile, const Config& config) {
    cout << "Generating stride load pattern (stride: " << config.stride << " bytes)..." << endl;
    
    for (uint64_t i = 0; i < config.num_instructions; i++) {
        uint64_t pc = BASE_PC + i * 4;
        // 고정 stride 패턴
        uint64_t addr = config.base_addr + (i * config.stride);
        // 다른 레지스터 사용 (의존성 없음)
        uint8_t reg = (i % MAX_REGS) + 1;
        
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
}

// Pattern 4: Random Loads
void generate_random_pattern(ofstream& outfile, const Config& config) {
    cout << "Generating random load pattern..." << endl;
    
    random_device rd;
    mt19937 gen(rd());
    // 주소 범위: base_addr ~ base_addr + 1GB
    uniform_int_distribution<uint64_t> addr_dist(config.base_addr, config.base_addr + (1ULL << 30));
    
    for (uint64_t i = 0; i < config.num_instructions; i++) {
        uint64_t pc = BASE_PC + i * 4;
        // 랜덤 주소 접근
        uint64_t addr = addr_dist(gen);
        // 캐시 라인 정렬 (64B 단위)
        addr = (addr / CACHE_LINE_SIZE) * CACHE_LINE_SIZE;
        // 다른 레지스터 사용 (의존성 없음)
        uint8_t reg = (i % MAX_REGS) + 1;
        
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
}

// Pattern 5: Mixed Pattern
void generate_mixed_pattern(ofstream& outfile, const Config& config) {
    cout << "Generating mixed load pattern..." << endl;
    
    uint64_t chunk_size = config.num_instructions / 4;
    uint64_t remaining = config.num_instructions % 4;
    
    // Sequential chunk
    for (uint64_t i = 0; i < chunk_size; i++) {
        uint64_t pc = BASE_PC + i * 4;
        uint64_t addr = config.base_addr + (i * 8);
        uint8_t reg = (i % MAX_REGS) + 1;
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
    
    // Stride chunk
    for (uint64_t i = 0; i < chunk_size; i++) {
        uint64_t pc = BASE_PC + (chunk_size + i) * 4;
        uint64_t addr = config.base_addr + (i * 256);
        uint8_t reg = ((chunk_size + i) % MAX_REGS) + 1;
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
    
    // Independent chunk
    for (uint64_t i = 0; i < chunk_size; i++) {
        uint64_t pc = BASE_PC + (chunk_size * 2 + i) * 4;
        uint64_t addr = config.base_addr + (i * CACHE_LINE_SIZE);
        uint8_t reg = ((chunk_size * 2 + i) % MAX_REGS) + 1;
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
    
    // Random chunk
    random_device rd;
    mt19937 gen(rd());
    uniform_int_distribution<uint64_t> addr_dist(config.base_addr, config.base_addr + (1ULL << 30));
    
    for (uint64_t i = 0; i < chunk_size + remaining; i++) {
        uint64_t pc = BASE_PC + (chunk_size * 3 + i) * 4;
        uint64_t addr = addr_dist(gen);
        addr = (addr / CACHE_LINE_SIZE) * CACHE_LINE_SIZE;
        uint8_t reg = ((chunk_size * 3 + i) % MAX_REGS) + 1;
        auto instr = generate_load_instruction(pc, addr, reg);
        write_instruction(outfile, instr);
    }
}

void print_usage(const char* prog_name) {
    cerr << "Usage: " << prog_name << " [OPTIONS]" << endl;
    cerr << "Options:" << endl;
    cerr << "  -p, --pattern <type>    Pattern type: independent, sequential, stride, random, mixed" << endl;
    cerr << "                          (default: independent)" << endl;
    cerr << "  -n, --num <count>       Number of instructions (default: 100000)" << endl;
    cerr << "  -s, --stride <bytes>    Stride size for stride pattern (default: 64)" << endl;
    cerr << "  -b, --base <addr>       Base memory address in hex (default: 0x10000000)" << endl;
    cerr << "  -o, --output <file>     Output trace file (default: output.trace)" << endl;
    cerr << "  -w, --window <size>     MLP window size for independent pattern (default: 10)" << endl;
    cerr << "  -h, --help              Show this help message" << endl;
}

PatternType parse_pattern(const string& pattern_str) {
    if (pattern_str == "independent") return PatternType::INDEPENDENT;
    if (pattern_str == "sequential") return PatternType::SEQUENTIAL;
    if (pattern_str == "stride") return PatternType::STRIDE;
    if (pattern_str == "random") return PatternType::RANDOM;
    if (pattern_str == "mixed") return PatternType::MIXED;
    cerr << "Warning: Unknown pattern '" << pattern_str << "', using independent" << endl;
    return PatternType::INDEPENDENT;
}

Config parse_args(int argc, char* argv[]) {
    Config config;
    
    for (int i = 1; i < argc; i++) {
        string arg = argv[i];
        
        if (arg == "-h" || arg == "--help") {
            print_usage(argv[0]);
            exit(0);
        } else if (arg == "-p" || arg == "--pattern") {
            if (i + 1 < argc) {
                config.pattern = parse_pattern(argv[++i]);
            } else {
                cerr << "Error: -p requires a pattern type" << endl;
                exit(1);
            }
        } else if (arg == "-n" || arg == "--num") {
            if (i + 1 < argc) {
                config.num_instructions = strtoull(argv[++i], nullptr, 10);
            } else {
                cerr << "Error: -n requires a number" << endl;
                exit(1);
            }
        } else if (arg == "-s" || arg == "--stride") {
            if (i + 1 < argc) {
                config.stride = strtoull(argv[++i], nullptr, 10);
            } else {
                cerr << "Error: -s requires a stride size" << endl;
                exit(1);
            }
        } else if (arg == "-b" || arg == "--base") {
            if (i + 1 < argc) {
                config.base_addr = strtoull(argv[++i], nullptr, 16);
            } else {
                cerr << "Error: -b requires a base address" << endl;
                exit(1);
            }
        } else if (arg == "-o" || arg == "--output") {
            if (i + 1 < argc) {
                config.output_file = argv[++i];
            } else {
                cerr << "Error: -o requires a filename" << endl;
                exit(1);
            }
        } else if (arg == "-w" || arg == "--window") {
            if (i + 1 < argc) {
                config.window_size = strtoull(argv[++i], nullptr, 10);
            } else {
                cerr << "Error: -w requires a window size" << endl;
                exit(1);
            }
        } else {
            cerr << "Error: Unknown option '" << arg << "'" << endl;
            print_usage(argv[0]);
            exit(1);
        }
    }
    
    return config;
}

int main(int argc, char* argv[]) {
    Config config = parse_args(argc, argv);
    
    cout << "MLP Trace Generator" << endl;
    cout << "===================" << endl;
    cout << "Pattern: ";
    switch (config.pattern) {
        case PatternType::INDEPENDENT: cout << "independent"; break;
        case PatternType::SEQUENTIAL: cout << "sequential"; break;
        case PatternType::STRIDE: cout << "stride"; break;
        case PatternType::RANDOM: cout << "random"; break;
        case PatternType::MIXED: cout << "mixed"; break;
    }
    cout << endl;
    cout << "Number of instructions: " << config.num_instructions << endl;
    cout << "Base address: 0x" << hex << config.base_addr << dec << endl;
    cout << "Output file: " << config.output_file << endl;
    cout << endl;
    
    ofstream outfile(config.output_file, ios::binary | ios::trunc);
    if (!outfile) {
        cerr << "Error: Cannot open output file '" << config.output_file << "'" << endl;
        return 1;
    }
    
    // Generate trace based on pattern
    switch (config.pattern) {
        case PatternType::INDEPENDENT:
            generate_independent_pattern(outfile, config);
            break;
        case PatternType::SEQUENTIAL:
            generate_sequential_pattern(outfile, config);
            break;
        case PatternType::STRIDE:
            generate_stride_pattern(outfile, config);
            break;
        case PatternType::RANDOM:
            generate_random_pattern(outfile, config);
            break;
        case PatternType::MIXED:
            generate_mixed_pattern(outfile, config);
            break;
    }
    
    outfile.close();
    
    // Verify file size
    ifstream check_file(config.output_file, ios::binary | ios::ate);
    if (check_file) {
        streamsize file_size = check_file.tellg();
        streamsize expected_size = config.num_instructions * sizeof(input_instr);
        cout << "Trace file generated successfully!" << endl;
        cout << "File size: " << file_size << " bytes" << endl;
        cout << "Expected size: " << expected_size << " bytes" << endl;
        if (file_size == expected_size) {
            cout << "Size verification: OK" << endl;
        } else {
            cout << "Warning: File size mismatch!" << endl;
        }
    }
    
    return 0;
}

