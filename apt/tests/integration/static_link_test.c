/* APT-02: exported static CcInfo must link without system library paths.
 * zlib's package closure includes libc's text libm.a GROUP script. */
#include <math.h>
#include <string.h>
#include <zlib.h>

int main(void) {
    const unsigned char input[] = "declared static library closure";
    unsigned char compressed[128], restored[128];
    uLongf compressed_size = sizeof compressed, restored_size = sizeof restored;
    double (*volatile square_root)(double) = sqrt;
    if (compress2(compressed, &compressed_size, input, sizeof input, 6) != Z_OK)
        return 1;
    if (uncompress(restored, &restored_size, compressed, compressed_size) != Z_OK)
        return 2;
    if (restored_size != sizeof input || memcmp(input, restored, sizeof input))
        return 3;
    return square_root(49.0) != 7.0;
}
