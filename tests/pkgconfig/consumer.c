#include <pkgfixture/api.h>
#include <pkgfixture/generated.h>
#include <json.h>
#include <stdint.h>
extern char fixture_marker;
int main(void) {
    struct json_object *object = json_object_new_int(42);
    int success = object && json_object_get_int(object) == 42 && fixture_value() == 42
        && FIXTURE_BASE + FIXTURE_OFFSET == 42 && (uintptr_t)&fixture_marker == 43;
    json_object_put(object);
    return success ? 0 : 1;
}
