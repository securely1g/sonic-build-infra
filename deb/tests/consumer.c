#include <stdio.h>

extern int metadata_probe_value(void);

int main(void) {
    printf("%d\n", metadata_probe_value());
    return metadata_probe_value() != 42;
}
