#include <sys/syscall.h>

int assembly_getpid_number(void);

int main(void) { return assembly_getpid_number() == SYS_getpid ? 0 : 1; }
