#include <stdint.h>
#include <stdio.h>
#include <sys/syscall.h>

#include "generated_value.h"

/* Linux assigns different syscall numbers to these architectures. Check the
 * selected headers without issuing a syscall from the other architecture. */
#if defined(__x86_64__)
_Static_assert(SYS_getpid == 39, "expected AMD64 Linux headers");
#elif defined(__aarch64__)
_Static_assert(SYS_getpid == 172, "expected ARM64 Linux headers");
#else
#error "unsupported sysroot test architecture"
#endif

int main(void) {
    uint64_t value = sonic_generated_value();
    if (value != 42 || sizeof(void*) != 8) return 1;
    printf("SONiC C sysroot %zu\n", sizeof(void*));
    return 0;
}
