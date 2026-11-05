#include <stdint.h>
#include <json-c/json.h>
extern int dynamic_value(void);
extern char duplicate_marker_0, duplicate_marker_299;
int main(void) {
    struct json_object *value = json_object_new_int(dynamic_value());
    int ok = json_object_get_int(value) == 17 && (uintptr_t)&duplicate_marker_0 == 1 && (uintptr_t)&duplicate_marker_299 == 1;
    json_object_put(value);
    return ok ? 0 : 1;
}
