CXX ?= c++

all: main.o

main.o: main.cpp
	$(CXX) -g -O0 -fno-inline -c -o main.o main.cpp

clean:
	rm -f *.o *.bc
