/* Exercise the registered libyang owner's original Bookworm dependency labels.
 * A valid package list alone cannot prove that locked CcInfo still provides
 * headers, linker inputs and the shared libraries needed when the program runs.
 */
#include <stddef.h>
#include <stdarg.h>
#include <setjmp.h>
#include <cmocka.h>
#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
#include <xxhash.h>

int main(void) {
    int error;
    PCRE2_SIZE offset;
    pcre2_code *pattern = pcre2_compile((PCRE2_SPTR)"^shared-[0-9]+$",
                                      PCRE2_ZERO_TERMINATED, 0, &error,
                                      &offset, NULL);
    assert_non_null(pattern);
    pcre2_match_data *match = pcre2_match_data_create_from_pattern(pattern, NULL);
    assert_non_null(match);
    assert_int_equal(pcre2_match(pattern, (PCRE2_SPTR)"shared-133", 10,
                                0, 0, match, NULL), 1);
    assert_true(XXH32("shared-133", 10, 0) != XXH32("different", 9, 0));
    pcre2_match_data_free(match);
    pcre2_code_free(pattern);
    return 0;
}
