#!/bin/sh
for argument do
    case "$argument" in --srcdir=*) source_directory=${argument#--srcdir=};; esac
done
exec python3 "$source_directory/response_fixture.py"
