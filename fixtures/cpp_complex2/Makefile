CXX ?= c++

all: main.o

main.o:	clean
	$(CXX) -O0 -fno-inline -fcoroutines -std=c++20 -Wno-unused-command-line-argument -c -o main.o main.cpp

clean:
	rm -rf *.o *.bc in* out* main
