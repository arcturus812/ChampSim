/*
 * Added for feeding infomation for traces
 * Edited by : hw park 
 * Date : 2024.12.03
 * email : hwpark@dgist.ac.kr
*/

#ifndef TRACE_FEEDER_H
#define TRACE_FEEDER_H

#include <string>
#include <vector>
#include <fstream>
#include <sstream>
#include <iostream>
#include <variant>
#include <regex>
#include <optional>
#include <unordered_map>
#include <cstdint>
#include <bitset>
#include <filesystem>

namespace champsim
{
    class tracefeeder
    {
        private:
            std::vector<std::string> filePaths;
            std::vector<std::string> traceNames; // Store trace names for mapping
            
            // Static shared data to avoid duplicate loading
            inline static std::unordered_map<std::string, std::unordered_map<uint64_t, bool>> shared_feed_data;
            
            // Extract trace name from file path
            std::string extractTraceName(const std::string& feedPath) {
                std::string filename = std::filesystem::path(feedPath).filename().string();
                size_t pos = filename.find("B.csv");
                if(pos != std::string::npos){
                    return filename.substr(0, pos + 1);
                }
                return "";
            }

        public:
            tracefeeder() = default;
            tracefeeder(const std::vector<std::string> fPaths) : filePaths(fPaths) {}
           
           bool readCSV() {
                for (const auto& filePath : filePaths) {
                    std::string traceName = extractTraceName(filePath);
                    if(traceName.empty()) {
                        std::cerr << "Error: Invalid feed file name format: " << filePath << std::endl;
                        return false;
                    }
                    
                    // Check if this trace data is already loaded
                    if(shared_feed_data.find(traceName) != shared_feed_data.end()) {
                        std::cout << "Feed data for trace '" << traceName << "' already loaded, reusing..." << std::endl;
                        traceNames.push_back(traceName);
                        continue;
                    }
                    
                    std::ifstream file(filePath);
                    if (!file.is_open()) {
                        std::cerr << "Error: File not found: " << filePath << std::endl;
                        return false;
                    }

                    std::string line;

                    // Read the first line to get column names (ignored as we know the column names)
                    if (!std::getline(file, line)) {
                        std::cerr << "Error: Empty file or unable to read column names." << std::endl;
                        return false;
                    }

                    std::unordered_map<uint64_t, bool> fileData;

                    // Read the rest of the data
                    while (std::getline(file, line)) {
                        std::stringstream lineStream(line);
                        std::string cell;

                        uint64_t vfn;
                        bool far;

                        // Read vfn (decimal to uint64_t)
                        if (std::getline(lineStream, cell, ',') && !cell.empty()) {
                            try {
                                vfn = std::stoull(cell);
                            } catch (const std::exception& e) {
                                std::cerr << "Error: Invalid vfn format: " << cell << std::endl;
                                continue;
                            }
                        } else {
                            std::cerr << "Error: Invalid vfn format." << std::endl;
                            continue;
                        }

                        // Read far (0 or 1 to bool)
                        if (std::getline(lineStream, cell, ',') && !cell.empty()) {
                            if (cell == "0") {
                                far = false;
                            } else if (cell == "1") {
                                far = true;
                            } else {
                                std::cerr << "Error: Invalid far format. Expected 0 or 1." << std::endl;
                                continue;
                            }
                        } else {
                            std::cerr << "Error: Invalid far format." << std::endl;
                            continue;
                        }

                        // Store the row with vfn as the key
                        fileData[vfn] = far;
                    }
                    file.close();

                    // Store the data in shared storage
                    shared_feed_data[traceName] = std::move(fileData);
                    traceNames.push_back(traceName);
                    std::cout << "Loaded feed data for trace: " << traceName << std::endl;
                }
                return true;
            }

            // Function to find a row given trace name and vfn key
            bool
            find(const std::string& traceName, uint64_t vfn) {
                // Find the trace data in shared storage
                auto trace_it = shared_feed_data.find(traceName);
                if (trace_it == shared_feed_data.end()) {
                    std::cerr << "Error: Trace not found: " << traceName << std::endl;
                    return false;
                }

                const auto& fileData = trace_it->second;

                // Find the row with the given vfn key
                auto it = fileData.find(vfn);
                if (it == fileData.end()) {
                    if(champsim::debug_print){
                        std::cerr << "Error: vfn not found " << vfn << " in trace " << traceName << std::endl;
                    }
                    return false;
                }

                return it->second;
            }

            // Legacy function for backward compatibility
            bool
            find(size_t feed_idx, uint64_t vfn) {
                if (feed_idx >= traceNames.size()) {
                    std::cerr << "Error: Invalid feed index." << std::endl;
                    return false;
                }
                return find(traceNames[feed_idx], vfn);
            }

            void printData() {
                for (const auto& [traceName, fileData] : shared_feed_data) {
                    std::cout << "Data for trace: " << traceName << std::endl;
                    for (const auto& [vfn, far] : fileData) {
                        std::cout << "vfn: " << vfn 
                                  << ", far: " << (far ? "1" : "0") << std::endl;
                    }
                    std::cout << "---------------------------------" << std::endl;
                }
            }
    };
}

#endif // TRACE_FEEDER_H
