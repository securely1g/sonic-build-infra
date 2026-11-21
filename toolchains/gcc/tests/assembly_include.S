/* Preprocessed assembly that includes a header from the shared sysroot. */
#include <sys/syscall.h>

        .text
        .globl  assembly_getpid_number
        .type   assembly_getpid_number, %function
assembly_getpid_number:
#if defined(__x86_64__)
        movl    $SYS_getpid, %eax
        ret
#elif defined(__aarch64__)
        mov     x0, #SYS_getpid
        ret
#else
#error "Unsupported architecture"
#endif
        .size   assembly_getpid_number, .-assembly_getpid_number
        .section .note.GNU-stack,"",%progbits
