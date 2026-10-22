AC_INIT([declared-libtool], [1.0])
AC_CONFIG_SRCDIR([probe.cc])
AM_INIT_AUTOMAKE([foreign])
AC_PROG_CC
AC_PROG_CXX
LT_INIT
AC_LANG_PUSH([C++])
AC_PREPROC_IFELSE([AC_LANG_SOURCE([[#include <vector>]])], [], [AC_MSG_ERROR([declared C++ preprocessor failed])])
AC_LANG_POP([C++])
AC_CONFIG_FILES([Makefile])
AC_OUTPUT
