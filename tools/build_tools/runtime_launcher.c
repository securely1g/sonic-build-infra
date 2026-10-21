/* Resolve the installed tool before entering its relocated shell wrapper.
 *
 * rules_foreign_cc exposes tools through symlinks in ext_build_deps/bin.
 * A shell's $0 names that symlink. Linux /proc/self/exe names this actual
 * executable, so the adjacent wrapper keeps its declared runtime root.
 * This bootstrap needs no external readlink or dynamic loader. /bin/sh
 * remains the existing interpreter for the generated wrapper script.
 */
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char executable[PATH_MAX];
    char script[PATH_MAX];
    ssize_t size = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (size < 0 || (size_t)size == sizeof(executable) - 1) {
        if (size >= 0) errno = ENAMETOOLONG;
        perror("build-tool executable path");
        return 127;
    }
    executable[size] = '\0';
    char *name = strrchr(executable, '/');
    if (name == NULL) {
        fputs("build-tool executable has no directory\n", stderr);
        return 127;
    }
    *name++ = '\0';
    int length = snprintf(script, sizeof(script), "%s/.%s.sh", executable, name);
    if (length < 0 || (size_t)length >= sizeof(script)) {
        fputs("build-tool wrapper path is too long\n", stderr);
        return 127;
    }
    char **arguments = calloc((size_t)argc + 2, sizeof(char *));
    if (arguments == NULL) {
        perror("build-tool arguments");
        return 127;
    }
    arguments[0] = "/bin/sh";
    arguments[1] = script;
    for (int index = 1; index < argc; ++index)
        arguments[index + 1] = argv[index];
    execv(arguments[0], arguments);
    perror(script);
    free(arguments);
    return 127;
}
