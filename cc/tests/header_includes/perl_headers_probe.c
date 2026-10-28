#include <EXTERN.h>
#include <perl.h>

_Static_assert(sizeof(IV) == IVSIZE, "Perl target integer ABI must match its configuration header");
_Static_assert(sizeof(void *) == PTRSIZE, "Perl target pointer ABI must match its configuration header");

int main(int argc, char **argv)
{
    char **environment = 0;
    Perl_sys_init3(&argc, &argv, &environment);
    return 0;
}
