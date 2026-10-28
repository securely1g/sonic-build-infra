// Debian Bookworm build-essential selects GCC12 and glibc2.36.
#include <gnu/libc-version.h>
#include <cstring>
#include <iostream>
#include <vector>
#if __GNUC__ != 12
#error Bookworm compiler must be GCC12
#endif
int main() {
    std::vector<int> values{20, 22};
    if (values[0] + values[1] != 42 || std::strcmp(gnu_get_libc_version(), "2.36")) return 1;
    std::cout << "Bookworm GCC12 glibc2.36 42\n";
    return 0;
}
