CC ?= cc
AR ?= ar

all: app

app: main.o libfirst.a libsecond.a
	$(CC) -o $@ main.o libfirst.a libsecond.a

libfirst.a: first_used.o first_dead.o
	$(AR) rcs $@ $^

libsecond.a: second_used.o second_dead.o
	$(AR) rcs $@ $^

%.o: %.c
	$(CC) -g -O0 -fno-inline -c -o $@ $<

clean:
	rm -f app *.o *.a *.bc *.llvm.manifest
