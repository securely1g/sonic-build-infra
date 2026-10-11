#include <stdio.h>
#include <unwind.h>

static _Unwind_Reason_Code frame(struct _Unwind_Context *context, void *argument)
{
    (void)context;
    ++*(int *)argument;
    return _URC_NO_REASON;
}

int main(void)
{
    int frames = 0;
    if (_Unwind_Backtrace(frame, &frames) != _URC_END_OF_STACK || frames == 0)
        return 1;
    puts("declared GCC link succeeded");
    return 0;
}
