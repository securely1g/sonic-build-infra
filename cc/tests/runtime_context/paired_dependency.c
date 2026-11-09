int first_indirect_value(void);
int second_indirect_value(void);

int paired_dependency_value(void) {
    return first_indirect_value() + second_indirect_value();
}
