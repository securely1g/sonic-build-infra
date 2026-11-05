#include <csignal>
#include <cstdlib>
#include <iostream>
#include <string>
#include <unistd.h>

extern const char *source_host_message();

int main(int argc, char **argv) {
    if (argc > 1 && std::string(argv[1]) == "exit") return 37;
    if (argc > 1 && std::string(argv[1]) == "signal") { raise(SIGTERM); return 1; }
    char cwd[4096];
    if (!getcwd(cwd, sizeof(cwd))) return 1;
    std::cout << source_host_message() << '\n' << cwd << '\n';
    for (int index = 1; index < argc; ++index) std::cout << argv[index] << '\n';
    return std::getenv("LD_LIBRARY_PATH") || std::getenv("LD_PRELOAD") || std::getenv("LD_AUDIT") ? 2 : 0;
}
