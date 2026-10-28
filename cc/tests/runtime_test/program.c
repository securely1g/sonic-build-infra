#include <gnu/libc-version.h>
#include <stdio.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
    /* MODULE.bazel's target snapshot fixes libc6 to Debian glibc 2.41. */
    if (strcmp(gnu_get_libc_version(), "2.41") || argc != 3) { fprintf(stderr,"glibc=%s argc=%d\n",gnu_get_libc_version(),argc); return 91; }
    if (!strcmp(argv[1], "raise-signal")) { raise(atoi(argv[2])); return 92; }
    if (!strcmp(argv[1], "python-environment")) {
        const char *home = getenv("PYTHONHOME"), *path = getenv("PYTHONPATH");
        printf("%s|%s\n", home ? home : "", path ? path : "");
        return atoi(argv[2]);
    }
    printf("target-glibc=%s argument=%s\n", gnu_get_libc_version(), argv[1]);
    return atoi(argv[2]);
}
