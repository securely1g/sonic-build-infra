#include "cc/tests/leaf/leaf.h"
#include "cc/tests/middle/middle.h"

int native_middle(void) { return native_leaf() + 1; }
