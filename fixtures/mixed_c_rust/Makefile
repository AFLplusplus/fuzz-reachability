CXX ?= c++

all: glue.o

glue.o: glue.cpp
	$(CXX) -g -O0 -fno-inline -c -o glue.o glue.cpp

clean:
	rm -f *.o *.bc
