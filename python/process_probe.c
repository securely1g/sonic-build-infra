/* Real Python extension for subprocess and native-library shutdown tests. */
#define Py_LIMITED_API 0x030B0000
#include <Python.h>

static PyObject *value(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    return PyLong_FromLong(42);
}

static PyMethodDef methods[] = {
    {"value", value, METH_NOARGS, "Return a value from the native extension."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_process_probe",
    NULL,
    -1,
    methods,
    NULL,
    NULL,
    NULL,
    NULL,
};

PyMODINIT_FUNC PyInit__process_probe(void) {
    return PyModule_Create(&module);
}
