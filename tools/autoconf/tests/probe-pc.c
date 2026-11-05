#include <marker.h>
#if DECLARED_PC_MARKER != 17
#error "The declared pkg-config header was not selected"
#endif
int declared_pc_marker(void) { return DECLARED_PC_MARKER; }
