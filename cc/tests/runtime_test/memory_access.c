#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    volatile int *values = malloc(2 * sizeof(int));
    if (!values) return 3;
    /* Index 0 is valid; index 2 deliberately exercises Memcheck's error exit. */
    values[atoi(argv[1])] = 42;
    free((void *)values);
    return 0;
}
