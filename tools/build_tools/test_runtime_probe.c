#include <stdlib.h>
char * volatile allocation;
int main(int argc, char **argv) {
    allocation = malloc(32);
    if (!allocation) return 2;
    allocation[0] = 0;
    if (argc > 1 && argv[1][0] == 'l') { allocation = 0; return 0; }
    free(allocation);
    if (argc > 1 && argv[1][0] == 'r') return allocation[0];
    allocation = 0;
    return 0;
}
