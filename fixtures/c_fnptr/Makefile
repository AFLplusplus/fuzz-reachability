CC ?= cc

all: main.o

main.o: main.c
	$(CC) -g -O0 -fno-inline -c -o main.o main.c

clean:
	rm -f *.o *.bc
