// These public headers include port_def.inc and port_undef.inc internally.
// Compiling through deb_import's CcInfo catches missing .inc exports.
#include <google/protobuf/descriptor.h>
#include <google/protobuf/message.h>

int main() { return GOOGLE_PROTOBUF_VERSION == 3021012 ? 0 : 1; }
