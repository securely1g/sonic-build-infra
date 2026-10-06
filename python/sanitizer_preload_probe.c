/* Like HWASan on an unsupported host, loading this fixture terminates Python. */
#include <unistd.h>

__attribute__((constructor)) static void fail_if_preloaded(void) {
    _exit(99);
}
