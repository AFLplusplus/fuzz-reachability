CC ?= cc

all: main.o

# A fuzz target has no main(), so we compile to an object (not an executable).
# Under gclang, get-bc extracts a single-module .bc from the object.
main.o: main.c
	$(CC) -g -O0 -fno-inline -c -o main.o main.c

clean:
	rm -f *.o *.bc
