CXX := g++
CXXFLAGS := -std=c++17 -Wall -Wextra -O3 -I../../inc
TARGET := trace_generator
SRC := trace_generator.cpp

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $<

clean:
	rm -f $(TARGET)

.PHONY: all clean

