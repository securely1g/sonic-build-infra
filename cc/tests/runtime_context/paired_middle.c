int paired_dependency_value(void);
int paired_context_middle(void);
int paired_context_middle(void) {
    return paired_dependency_value() + 1;
}
