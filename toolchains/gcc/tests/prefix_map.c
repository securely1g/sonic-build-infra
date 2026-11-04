/* Debian dpkg-buildflags maps the compiler working directory to '.'. */
const char *compiled_source_path = __FILE__;
int prefix_map_fixture(void) { return 42; }
