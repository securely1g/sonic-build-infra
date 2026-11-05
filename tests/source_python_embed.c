#include <Python.h>

int main(void)
{
    Py_Initialize();
    int result = PyRun_SimpleString(
        "import encodings, json, sys, caller_input\n"
        "print(json.dumps({'prefix': sys.prefix, 'stdlib': encodings.__file__, "
        "'version': list(sys.version_info[:2]), 'value': caller_input.value}))\n");
    int final_result = Py_FinalizeEx();
    return result || final_result < 0;
}
